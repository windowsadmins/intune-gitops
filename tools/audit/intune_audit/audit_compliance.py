"""
Compliance policies — coverage and enforcement gaps.

  - unassigned    : a compliance policy assigned to nothing is a coverage gap
                    (and risks devices being treated compliant-by-default)
  - notify-only   : policy whose scheduled actions never block/retire — it reports
                    but never enforces
  - noncompliant  : policies with devices currently non-compliant or in error
"""
from .common import Report, split_targets


def run(client, args) -> Report:
    rep = Report("compliance")
    try:
        policies = client.get(
            "deviceManagement/deviceCompliancePolicies"
            "?$expand=assignments,scheduledActionsForRule($expand=scheduledActionConfigurations)&$top=200")
    except Exception as e:
        rep.note(f"could not enumerate compliance policies ({str(e)[:80]})")
        return rep
    rep.note(f"{len(policies)} compliance policy(ies)")

    for p in policies:
        name = p.get("displayName") or p.get("id")
        tgt = split_targets(p.get("assignments"))
        if not tgt["include"] and not tgt["all_devices"] and not tgt["all_users"]:
            rep.add("high", "unassigned", name, "no assignment — coverage gap")

        # enforcement: is any action ever a block/retire (vs notify-only)?
        actions = set()
        for rule in p.get("scheduledActionsForRule") or []:
            for cfg in rule.get("scheduledActionConfigurations") or []:
                if cfg.get("actionType"):
                    actions.add(cfg["actionType"])
        if actions and not ({"block", "retire", "remoteLock"} & actions):
            rep.add("low", "notify-only", name, f"actions {sorted(actions)} never block/retire")

        # current non-compliant / error counts
        try:
            ov = client.get_one(f"deviceManagement/deviceCompliancePolicies/{p['id']}/deviceStatusOverview")
            nc, err = ov.get("nonCompliantDeviceCount", 0), ov.get("errorDeviceCount", 0)
            if nc or err:
                rep.add("medium", "noncompliant", name,
                        f"{nc} non-compliant, {err} error device(s)")
        except Exception:
            pass

    return rep
