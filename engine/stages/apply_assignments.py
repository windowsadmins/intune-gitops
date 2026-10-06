#!/usr/bin/env python3
"""Write the plan to the tenant.

Read stages/plan_assignments.py first -- this stage does nothing the plan did
not already show you.

The one sentence that governs this file: the assignment API REPLACES the whole
assignment set. You do not add a group to a policy; you send the complete list
of who it applies to, and whatever you send becomes the truth. Sending an empty
list is a successful call that unassigns the policy from every device.

That is also exactly what this code produces if the manifest walk returns
nothing. Hence the floor, and hence whatIf being a supported way to run rather
than a debug flag.

What it writes, per key:

  managed_profiles  deviceConfigurations and Settings Catalog configurationPolicies
  managed_scripts   Windows: deviceHealthScripts (remediations)
                    macOS:   deviceShellScripts
  managed_apps      mobileApps, as required installs

A conditional entry is assigned with a real assignment filter: the filter is
found or created from the condition and its id goes on the target. An entry
whose condition cannot be expressed is never assigned unfiltered.

    WHATIF=true python3 -m stages.apply_assignments --platform windows path/to/manifests
"""

from __future__ import annotations

import argparse
import collections
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "enrollment"))

from conditions import (
    PLATFORMS,
    CondParseError,
    UnsupportedCondition,
    get_translator,
)  # noqa: E402
from lib import guards  # noqa: E402
from lib.filters import FilterStore, group_target, target_filter_id  # noqa: E402
from stages.plan_assignments import build_plan, resolve_exclusions  # noqa: E402

log = logging.getLogger(__name__)

GRAPH_BETA = "https://graph.microsoft.com/beta"
MANAGED_MARKER = os.getenv("INTUNE_MANAGED_MARKER", "managed-by-gitops")

# Remediations run daily; runRemediationScript is always True. An assignment
# with it False is Intune's detect-only mode -- detection runs and reports, the
# remediation never executes -- and nothing in any summary shows it.
RUN_SCHEDULE = {
    "@odata.type": "#microsoft.graph.deviceHealthScriptDailySchedule",
    "interval": 1,
    "time": "01:00:00",
    "useUtc": False,
}


class Kind:
    """One Intune object type: where it lives, what names it, how it is assigned."""

    def __init__(self, label, endpoint, name_field, body_key, extra=None,
                 area="deviceManagement"):
        self.label, self.endpoint, self.name_field = label, endpoint, name_field
        self.body_key, self.extra, self.area = body_key, extra or {}, area

    def url(self, suffix=""):
        return f"{GRAPH_BETA}/{self.area}/{self.endpoint}{suffix}"


PROFILE_KINDS = [
    Kind("configuration profile", "deviceConfigurations", "displayName", "assignments"),
    Kind("settings catalog policy", "configurationPolicies", "name", "assignments"),
]
SCRIPT_KINDS = {
    "windows": [
        Kind(
            "remediation",
            "deviceHealthScripts",
            "displayName",
            "deviceHealthScriptAssignments",
            {"runRemediationScript": True, "runSchedule": RUN_SCHEDULE},
        )
    ],
    "macos": [
        Kind(
            "shell script",
            "deviceShellScripts",
            "displayName",
            "deviceManagementScriptAssignments",
        )
    ],
}
APP_KINDS = [
    Kind(
        "app",
        "mobileApps",
        "displayName",
        "mobileAppAssignments",
        {"@odata.type": "#microsoft.graph.mobileAppAssignment", "intent": "required"},
         area="deviceAppManagement",
    ),
]


def is_ours(obj: dict) -> bool:
    """Only objects carrying the marker are ever modified. Everything else in the
    tenant belongs to somebody else -- created in the portal, inherited from a
    migration, deliberately hand-managed."""
    return MANAGED_MARKER in (obj.get("description") or "")


def load_protected() -> set[str]:
    """Profiles another pipeline owns, which this one must not touch.

    Two pipelines that both do a full replace on the same object take turns
    winning. When two automated systems can write the same object, one of them
    has to be able to name what it does not own, in a file the other reads.
    """
    path = pathlib.Path(os.getenv("PROTECTED_PROFILES", "protected-profiles.yaml"))
    if not path.exists():
        return set()
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    names: set[str] = set()
    for section in ("All", "Assigned", "Shared"):
        for identifier in data.get(section) or []:
            if isinstance(identifier, str) and identifier.strip():
                names.add(identifier.strip().rsplit(".", 1)[-1])
    return names


def _shape(assignments, owned_filter_ids) -> set[tuple]:
    """Comparable form of an assignment set: (type, group, filter)."""
    out = set()
    for a in assignments or []:
        t = a.get("target") or {}
        out.add(
            (
                t.get("@odata.type"),
                t.get("groupId"),
                target_filter_id(t, owned_filter_ids),
            )
        )
    return out


def apply(
    root: pathlib.Path,
    *,
    platform: str | None = None,
    what_if: bool = False,
    client=None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")
    translator = get_translator(platform)

    assignments, exclusions, skipped = build_plan(root, translator)
    if skipped:
        log.error(
            "%d manifest entr(ies) could not be rendered. Run the linter; "
            "refusing to apply a partial plan.",
            len(skipped),
        )
        return 1

    live_exclusions, inert = resolve_exclusions(assignments, exclusions)
    for name, group in inert:
        log.info(
            "Inert exclusion, not sent: %s from %s (not inherited there).", name, group
        )

    guards.assignment_floor(len(assignments))

    if client is None:
        from shared.graph import GraphClient

        client = GraphClient(what_if=what_if)
    if not client.token:
        log.error("No GRAPH_TOKEN. Run stages.plan_assignments for an offline plan.")
        return 1

    protected = load_protected()
    if protected:
        log.info(
            "Owned by another pipeline, skipped here: %s", ", ".join(sorted(protected))
        )

    filters = FilterStore(client, translator)
    owned_filter_ids = filters.owned_ids()

    excluded_by_name: dict[str, list[str]] = collections.defaultdict(list)
    for name, group in live_exclusions:
        excluded_by_name[name].append(group)

    kinds_for_key = {
        "managed_profiles": PROFILE_KINDS,
        "managed_scripts": SCRIPT_KINDS[translator.name],
        "managed_apps": APP_KINDS,
    }

    failures = applied = unchanged = missing = 0
    for key, kinds in kinds_for_key.items():
        wanted: dict[str, list] = collections.defaultdict(list)
        for a in assignments:
            if a.key == key and not (key == "managed_profiles" and a.name in protected):
                wanted[a.name].append(a)
        if not wanted:
            continue

        existing: dict[str, tuple[Kind, dict]] = {}
        for kind in kinds:
            for obj in client.paged(kind.url("?$expand=assignments")):
                name = obj.get(kind.name_field)
                if name and is_ours(obj):
                    existing.setdefault(name, (kind, obj))

        for name, targets in sorted(wanted.items()):
            if name not in existing:
                log.warning(
                    "%s: manifest references '%s' but no object carrying the "
                    "managed marker exists in the tenant.",
                    key,
                    name,
                )
                missing += 1
                continue
            kind, obj = existing[name]

            spec: list[dict] = []
            for a in targets:
                gid = client.group_id(a.group)
                if not gid:
                    log.warning(
                        "  %s: group %s not found, skipping that target.", name, a.group
                    )
                    continue
                filter_id = None
                if a.cond:
                    try:
                        filter_id = filters.ensure(a.cond)
                    except (UnsupportedCondition, CondParseError) as exc:
                        # Never fall back to unfiltered: that reaches every
                        # device in the group instead of the subset meant.
                        log.error(
                            "  %s -> %s: %s; target not sent.", name, a.group, exc
                        )
                        failures += 1
                        continue
                spec.append({**kind.extra, "target": group_target(gid, filter_id)})

            for group in excluded_by_name.get(name, []):
                gid = client.group_id(group)
                if gid:
                    spec.append(
                        {**kind.extra, "target": group_target(gid, exclude=True)}
                    )

            if not spec:
                log.error(
                    "  %s resolved to no targets -- refusing to send an empty "
                    "assignment set.",
                    name,
                )
                failures += 1
                continue

            if _shape(spec, None) == _shape(obj.get("assignments"), owned_filter_ids):
                unchanged += 1
                continue

            log.info(
                "%s '%s' -> %d target(s), %d exclusion(s)",
                kind.label,
                name,
                len(targets),
                len(excluded_by_name.get(name, [])),
            )
            resp = client.post(
                kind.url(f"/{obj['id']}/assign"), json={kind.body_key: spec}
            )
            if resp is not None and resp.status_code >= 400:
                log.error(
                    "  assign failed for %s: %s %s",
                    name,
                    resp.status_code,
                    resp.text[:300],
                )
                failures += 1
                continue
            applied += 1

    log.info(
        "Applied %d, unchanged %d, absent from the tenant %d, failed %d.",
        applied,
        unchanged,
        missing,
        failures,
    )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "manifests", nargs="?", type=pathlib.Path, default=pathlib.Path("manifests")
    )
    ap.add_argument(
        "--platform",
        choices=PLATFORMS,
        help="windows (Cimian) or macos (Munki); default $INTUNE_PLATFORM",
    )
    ap.add_argument("--what-if", action="store_true")
    args = ap.parse_args(argv)
    what_if = args.what_if or os.getenv("WHATIF", "").strip().lower() == "true"
    return apply(args.manifests, platform=args.platform, what_if=what_if)


if __name__ == "__main__":
    raise SystemExit(main())
