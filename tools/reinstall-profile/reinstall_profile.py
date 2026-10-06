#!/usr/bin/env python3
"""
reinstall_profile -- force a clean reinstall of one Intune config profile on one or
many devices, via the exclusion-group cycle.

Why this exists: a macOS/Windows configuration profile can report `remediated` in
Intune (the device *acknowledged* the assignment) while the payload never actually
installed properly. VPN is the common victim -- the symptom users report is "the
shared secret disappeared / it keeps trying to connect with a certificate". You cannot
fix it device-side: MDM-delivered profiles are locked and only the MDM server can
remove them. The reliable server-side fix is to make MDM remove and reinstall the
profile:

    1. Add the target device(s) to a temporary Entra exclusion group.
    2. Point that group at the profile as an *exclusion* (exclude beats include), so
       MDM removes the profile from just those devices -- nothing else is touched.
    3. Wait until the profile is gone from each device.
    4. Drop the exclusion, so MDM reinstalls the profile clean.
    5. Wait until it is back, then delete the temporary group.

The profile's existing include assignments are captured first and restored exactly, so
the assignment ends the run identical to how it started. If anything fails mid-cycle,
the finally-block restores the original assignments and deletes the temp group, so a
half-finished run never leaves a device without its profile or a dangling exclusion.

Auth: everything goes through the Azure CLI (`az rest`, `az ad group`), so it acts
as whoever ran `az login`. That identity needs, in Microsoft Graph:
  * DeviceManagementConfiguration.ReadWrite.All  -- read and reassign the profile
  * DeviceManagementManagedDevices.ReadWrite.All -- read device state, send a sync
  * Group.ReadWrite.All, GroupMember.ReadWrite.All, Device.Read.All
                                                 -- the temporary exclusion group

Timing seen in practice: removal ~5 min, reinstall ~11 min, so the 30-min-per-phase
default timeout has comfortable headroom.

Usage:
    # dry run (default) -- resolves everything and prints the plan, writes nothing
    python3 reinstall_profile.py --profile CorpVPN --serials SERIAL001

    # actually do it, one or many devices
    python3 reinstall_profile.py --profile CorpVPN --serials SERIAL001,SERIAL002 --apply

Requires: the Azure CLI on PATH, signed in with `az login`.
"""

import argparse
import json
import subprocess
import sys
import time

GRAPH = "https://graph.microsoft.com/beta"
GRAPH_V1 = "https://graph.microsoft.com/v1.0"


def az(*args):
    """Run one Azure CLI command and return stdout. Raises SystemExit on a non-zero
    exit so the caller's finally-block runs."""
    res = subprocess.run(["az", *args], capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        raise SystemExit(f"az {args[0]} {args[1] if len(args) > 1 else ''} failed ({res.returncode})")
    return res.stdout.strip()


def graph(url, method="get", body=None):
    """GET/POST a Graph URL through `az rest` and parse the JSON reply, if any."""
    args = ["rest", "--method", method, "--url", url, "-o", "json"]
    if body is not None:
        args += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    out = az(*args)
    return json.loads(out) if out else {}


# --------------------------------------------------------------------------- resolve


def resolve_profile(name):
    """displayName -> (id, odata_type). Errors clearly if 0 or >1 match."""
    url = (
        f"{GRAPH}/deviceManagement/deviceConfigurations"
        f"?$filter=displayName eq '{name}'&$select=id,displayName"
    )
    hits = graph(url).get("value", [])
    if not hits:
        raise SystemExit(f"No config profile named '{name}'.")
    if len(hits) > 1:
        ids = ", ".join(h["id"] for h in hits)
        raise SystemExit(f"'{name}' is ambiguous ({len(hits)} matches): {ids}")
    return hits[0]["id"]


def current_assignments(profile_id):
    """The profile's existing assignments as a list of {'target': ...} dicts, ready to
    re-POST to /assign. We keep only the target (the rest is server-assigned)."""
    url = f"{GRAPH}/deviceManagement/deviceConfigurations/{profile_id}/assignments"
    out = []
    for a in graph(url).get("value", []):
        tgt = a.get("target")
        if tgt:
            out.append({"target": tgt})
    return out


def resolve_devices(serials):
    """serial -> {serial, managed_id, object_id, name}. Two hops:
    managedDevices gives the Intune id + azureADDeviceId; the directory /devices lookup
    turns azureADDeviceId into the Entra device *object* id (what group membership needs).
    Intune read via devices, directory read via identity.
    """
    devices = []
    for s in serials:
        s = s.strip().upper()
        if not s:
            continue
        murl = (
            f"{GRAPH}/deviceManagement/managedDevices"
            f"?$filter=serialNumber eq '{s}'"
            f"&$select=id,deviceName,azureADDeviceId,operatingSystem"
        )
        md = graph(murl).get("value", [])
        if not md:
            raise SystemExit(f"Serial {s}: not enrolled in Intune (no managedDevice).")
        md = md[0]
        aad = md.get("azureADDeviceId")
        durl = f"{GRAPH_V1}/devices?$filter=deviceId eq '{aad}'&$select=id,displayName"
        dj = graph(durl).get("value", [])
        if not dj:
            raise SystemExit(f"Serial {s}: no Entra device object for deviceId {aad}.")
        devices.append(
            {
                "serial": s,
                "managed_id": md["id"],
                "object_id": dj[0]["id"],
                "name": md.get("deviceName") or dj[0].get("displayName") or s,
            }
        )
    return devices


# ----------------------------------------------------------------------------- state


def profile_state(managed_id, profile_id_name):
    """Current state string of the profile on one device, or 'ABSENT' if not present.
    Matched by displayName because deviceConfigurationStates keys on the profile name."""
    url = (
        f"{GRAPH}/deviceManagement/managedDevices/{managed_id}"
        f"/deviceConfigurationStates"
    )
    for st in graph(url).get("value", []):
        if st.get("displayName") == profile_id_name:
            return st.get("state") or "unknown"
    return "ABSENT"


def sync(managed_id):
    az("rest", "--method", "post", "--url",
       f"{GRAPH}/deviceManagement/managedDevices/{managed_id}/syncDevice", "-o", "none")


def poll_until(devices, profile_name, want, timeout_min, interval_s):
    """Poll every device until its profile state satisfies `want(state)`, or timeout.
    Returns the set of serials that reached the target. Prints progress per round."""
    deadline = time.monotonic() + timeout_min * 60
    remaining = {d["serial"]: d for d in devices}
    done = set()
    while remaining and time.monotonic() < deadline:
        for serial, d in list(remaining.items()):
            state = profile_state(d["managed_id"], profile_name)
            stamp = time.strftime("%H:%M:%S")
            if want(state):
                print(f"  {stamp}  {serial:<14} {profile_name} -> {state}  [done]")
                done.add(serial)
                del remaining[serial]
            else:
                print(f"  {stamp}  {serial:<14} {profile_name} -> {state}")
        if remaining:
            time.sleep(interval_s)
    if remaining:
        late = ", ".join(remaining)
        print(f"  TIMEOUT after {timeout_min} min; still waiting on: {late}")
    return done


# ------------------------------------------------------------------------------ main


def build_assignments(originals, exclusion_group_id=None):
    """The assignments list to POST: the originals, optionally plus an exclusion."""
    payload = [dict(a) for a in originals]
    if exclusion_group_id:
        payload.append(
            {
                "target": {
                    "@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget",
                    "groupId": exclusion_group_id,
                }
            }
        )
    return {"assignments": payload}


def set_assignments(profile_id, assignments_body):
    url = f"{GRAPH}/deviceManagement/deviceConfigurations/{profile_id}/assign"
    graph(url, method="post", body=assignments_body)


def main():
    ap = argparse.ArgumentParser(
        description="Force-reinstall an Intune config profile via the exclusion-group cycle."
    )
    ap.add_argument(
        "--profile",
        required=True,
        help="config profile displayName (e.g. CorpVPN)",
    )
    ap.add_argument("--serials", required=True, help="comma-separated device serial(s)")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="perform the cycle (default: dry-run plan only)",
    )
    ap.add_argument(
        "--timeout", type=int, default=30, help="minutes to wait per phase (default 30)"
    )
    ap.add_argument(
        "--poll-interval",
        type=int,
        default=90,
        help="seconds between state polls (default 90)",
    )
    args = ap.parse_args()

    serials = [s for s in (args.serials or "").split(",") if s.strip()]
    if not serials:
        raise SystemExit("No serials given.")

    print(f"Resolving profile '{args.profile}' and {len(serials)} device(s)...")
    profile_id = resolve_profile(args.profile)
    originals = current_assignments(profile_id)
    devices = resolve_devices(serials)

    print(f"\nProfile : {args.profile}  ({profile_id})")
    print(f"Existing assignments preserved: {len(originals)} target(s)")
    print("Devices :")
    for d in devices:
        print(f"  {d['serial']:<14} {d['name']:<28} managed={d['managed_id']}")

    if not args.apply:
        print("\n[DRY RUN] Would, for the device(s) above:")
        print("  1. create a temp Entra exclusion group and add them as members")
        print(
            f"  2. exclude that group from '{args.profile}' -> MDM removes the profile"
        )
        print(f"  3. wait (<= {args.timeout} min) for removal, then drop the exclusion")
        print("  4. wait for clean reinstall, then delete the temp group")
        print("\nRe-run with --apply to perform it.")
        return

    # A group name that is stable per-run without needing a clock:
    # profile + first serial keeps it human-legible and unique enough for a temp object.
    suffix = f"{args.profile}-{devices[0]['serial']}".lower().replace(" ", "-")[:60]
    group_name = f"Intune-Reinstall-Exclude-{suffix}"
    nick = ("intune-reinstall-" + suffix).replace("_", "-")[:60]

    group_id = None
    exclusion_live = False
    try:
        print(f"\nCreating temp exclusion group '{group_name}'...")
        group_id = (
            az("ad", "group", "create", "--display-name", group_name,
               "--mail-nickname", nick,
               "--description", "Temporary Intune profile-reinstall exclusion - delete after use",
               "--query", "id", "-o", "tsv")
            .splitlines()[-1]
            .strip()
        )
        print(f"  group {group_id}")
        for d in devices:
            az("ad", "group", "member", "add", "--group", group_id,
               "--member-id", d["object_id"])
        print(f"  added {len(devices)} device object(s) as members")

        print("\nApplying exclusion (MDM will remove the profile)...")
        set_assignments(profile_id, build_assignments(originals, group_id))
        exclusion_live = True
        for d in devices:
            sync(d["managed_id"])

        print(f"\nWaiting for removal (<= {args.timeout} min):")
        poll_until(
            devices,
            args.profile,
            lambda s: s == "ABSENT" or s == "notApplicable",
            args.timeout,
            args.poll_interval,
        )

        print("\nDropping exclusion (MDM will reinstall the profile clean)...")
        set_assignments(profile_id, build_assignments(originals))
        exclusion_live = False
        for d in devices:
            sync(d["managed_id"])

        print(f"\nWaiting for reinstall (<= {args.timeout} min):")
        back = poll_until(
            devices,
            args.profile,
            lambda s: s == "remediated" or s == "compliant",
            args.timeout,
            args.poll_interval,
        )

        print(f"\nReinstalled on {len(back)}/{len(devices)} device(s).")
    finally:
        # Safety net: never leave the profile excluded, never leave the temp group behind.
        if exclusion_live:
            print(
                "\n[cleanup] restoring original assignments (exclusion still live)..."
            )
            set_assignments(profile_id, build_assignments(originals))
        if group_id:
            print("[cleanup] deleting temp exclusion group...")
            az("ad", "group", "delete", "--group", group_id)
    print("\nDone.")


if __name__ == "__main__":
    main()
