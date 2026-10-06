"""
Apps — intent conflicts.

The app-specific failure mode is intent collision: the same group assigned an app
as install (required/available) AND uninstall, or required AND available. Intune
won't resolve those coherently. Orphaned/broad app assignments are covered by the
assignments module; this focuses on intent.
"""
from collections import defaultdict

from .common import Report

INSTALL_INTENTS = {"required", "available", "availableWithoutEnrollment"}


def run(client, args) -> Report:
    rep = Report("apps")
    try:
        apps = client.get("deviceAppManagement/mobileApps?$expand=assignments&$top=200")
    except Exception as e:
        rep.note(f"could not enumerate mobileApps ({str(e)[:80]})")
        return rep

    assigned = [a for a in apps if a.get("assignments")]
    rep.note(f"{len(apps)} apps ({len(assigned)} with assignments)")

    for app in assigned:
        name = app.get("displayName") or app.get("id")
        group_intents = defaultdict(set)  # groupId -> {intent}
        for asg in app.get("assignments") or []:
            t = asg.get("target", {})
            gid = t.get("groupId")
            intent = asg.get("intent")
            if gid and intent:
                group_intents[gid].add(intent)
        for gid, intents in group_intents.items():
            gname = client.group_name(gid) or gid
            if "uninstall" in intents and (intents & INSTALL_INTENTS):
                rep.add("high", "intent-conflict", name,
                        f"group {gname}: both uninstall and {sorted(intents & INSTALL_INTENTS)}")
            elif "required" in intents and "available" in intents:
                rep.add("low", "redundant-intent", name,
                        f"group {gname}: both required and available")
    return rep
