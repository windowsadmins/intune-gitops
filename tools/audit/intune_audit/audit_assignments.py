"""
Assignment hygiene — cross-object.

Walks every assignable Intune object type and flags assignment problems that
recur across the tenant, regardless of platform or object kind:

  - orphan        : assigned to a group that no longer exists (the dead-group bug)
  - empty-group   : assigned to a group that exists but has 0 members (reaches nothing)
  - include+exclude: same group both included and excluded on one object
  - broad-target  : targets All Devices / All Licensed Users
  - unassigned    : object exists in Intune but has no assignment at all

This is the generalisation of the per-config orphan check to apps, compliance,
scripts, update profiles, ADMX, etc.
"""
from .common import Report, split_targets

# (label, graph path, display-name field). Each is fetched with $expand=assignments.
OBJECT_TYPES = [
    ("config profile",        "deviceManagement/deviceConfigurations",          "displayName"),
    ("settings catalog",      "deviceManagement/configurationPolicies",         "name"),
    ("compliance policy",     "deviceManagement/deviceCompliancePolicies",      "displayName"),
    ("ADMX profile",          "deviceManagement/groupPolicyConfigurations",     "displayName"),
    ("PowerShell script",     "deviceManagement/deviceManagementScripts",       "displayName"),
    ("shell script",          "deviceManagement/deviceShellScripts",            "displayName"),
    ("remediation",           "deviceManagement/deviceHealthScripts",           "displayName"),
    ("feature update",        "deviceManagement/windowsFeatureUpdateProfiles",  "displayName"),
    ("quality update",        "deviceManagement/windowsQualityUpdateProfiles",  "displayName"),
    ("hotpatch policy",       "deviceManagement/windowsQualityUpdatePolicies",  "displayName"),
    ("driver update",         "deviceManagement/windowsDriverUpdateProfiles",   "displayName"),
    ("app",                   "deviceAppManagement/mobileApps",                 "displayName"),
]

# Several Windows-update endpoints cap $top at 200; 200 + nextLink paging is safe
# everywhere (GraphClient.get follows @odata.nextLink).
PAGE = 200

# Built-in / store apps are mostly unassigned by design — don't cry "unassigned"
# for the entire mobileApps catalog (hundreds of store entries).
SKIP_UNASSIGNED = {"app"}


def run(client, args) -> Report:
    rep = Report("assignments")
    objects = []  # (label, name, target_split)
    all_group_ids = set()

    for label, path, name_field in OBJECT_TYPES:
        try:
            items = client.get(f"{path}?$expand=assignments&$top={PAGE}")
        except Exception as e:
            rep.note(f"{label}: could not enumerate ({str(e)[:80]})")
            continue
        for it in items:
            name = it.get(name_field) or it.get("name") or it.get("id")
            tgt = split_targets(it.get("assignments"))
            objects.append((label, name, tgt))
            all_group_ids |= tgt["include"] | tgt["exclude"]
        rep.note(f"{label}: {len(items)} object(s)")

    alive = client.groups_alive(all_group_ids)
    member_counts = client.group_member_counts(alive)

    for label, name, tgt in objects:
        inc, exc = tgt["include"], tgt["exclude"]

        dead = [g for g in inc | exc if g not in alive]
        if dead:
            rep.add("high", "orphan", f"{label}: {name}",
                    f"assigned to {len(dead)} deleted group(s): {sorted(dead)}")

        both = inc & exc
        if both:
            rep.add("medium", "include+exclude", f"{label}: {name}",
                    f"{len(both)} group(s) both included and excluded: {sorted(both)}")

        empty = [g for g in inc if g in alive and member_counts.get(g, 0) == 0]
        if empty:
            names = [client.group_name(g) or g for g in empty]
            rep.add("low", "empty-group", f"{label}: {name}",
                    f"included group(s) with 0 members: {sorted(names)}")

        if tgt["all_devices"] or tgt["all_users"]:
            which = ", ".join(t for t, on in
                              (("All Devices", tgt["all_devices"]), ("All Users", tgt["all_users"])) if on)
            rep.add("info", "broad-target", f"{label}: {name}", f"targets {which}")

        if (label not in SKIP_UNASSIGNED and not inc and not exc
                and not tgt["all_devices"] and not tgt["all_users"]):
            rep.add("low", "unassigned", f"{label}: {name}", "no assignment — reaches no devices")

    return rep
