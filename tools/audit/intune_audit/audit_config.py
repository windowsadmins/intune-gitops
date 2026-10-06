"""
Configuration profiles — conflicts, same-setting overlap, error hotspots.

Conflicts and error hotspots come from the per-device status export; overlap is
derived from live assignments (predicts conflicts before any device reports).
Orphaned-assignment and broad-target checks live in the assignments module.
"""
from collections import defaultdict

from .common import Report, split_targets


def _base(name: str) -> str:
    for pre in ("Assigned", "Shared"):
        if name.startswith(pre):
            return name[len(pre):]
    return name


def run(client, args) -> Report:
    rep = Report("config")

    # ---- live overlap: same-setting-family variants sharing an include group --
    profiles = []
    for path, field in (("deviceManagement/deviceConfigurations", "displayName"),
                        ("deviceManagement/configurationPolicies", "name")):
        try:
            for p in client.get(f"{path}?$expand=assignments&$top=200"):
                profiles.append((p.get(field) or p.get("name"), split_targets(p.get("assignments"))["include"]))
        except Exception as e:
            rep.note(f"could not enumerate {path}: {str(e)[:80]}")
    group_to_names = defaultdict(list)
    for name, inc in profiles:
        for g in inc:
            group_to_names[g].append(name)
    overlaps = defaultdict(set)
    for g, names in group_to_names.items():
        for a in names:
            for b in names:
                if a < b and _base(a) == _base(b):
                    overlaps[(a, b)].add(g)
    for (a, b), gs in sorted(overlaps.items(), key=lambda kv: -len(kv[1])):
        rep.add("high", "overlap", f"{a}  <>  {b}",
                f"share {len(gs)} include group(s) — same setting family, latent conflict")

    # ---- device-reported conflicts + error hotspots (status export) -----------
    try:
        rows = client.export_report("DeviceConfigurationPolicyStatuses")
    except Exception as e:
        rep.note(f"status export unavailable ({str(e)[:80]}) — conflict/error sections skipped")
        return rep

    conf_devs = defaultdict(set)      # policy -> {deviceId}
    dev_errors = defaultdict(lambda: {"name": "", "n": 0})
    for x in rows:
        d, s = x["IntuneDeviceId"], x["PolicyStatus"]
        if s == "6":
            conf_devs[x["PolicyName"]].add(d)
        elif s == "5":
            dev_errors[d]["name"] = x["DeviceName"]
            dev_errors[d]["n"] += 1

    total_conf_devs = len({d for s in conf_devs.values() for d in s})
    if conf_devs:
        rep.note(f"{len(conf_devs)} policies in conflict across {total_conf_devs} device(s)")
    for policy, devs in sorted(conf_devs.items(), key=lambda kv: -len(kv[1])):
        rep.add("high", "conflict", policy, f"{len(devs)} device(s) reporting conflict")

    threshold = getattr(args, "error_threshold", 10)
    hot = sorted((v for v in dev_errors.values() if v["n"] >= threshold), key=lambda v: -v["n"])
    cap = 30
    for v in hot[:cap]:
        rep.add("medium", "error-hotspot", v["name"], f"failing {v['n']} policies (device/fleet health)")
    if len(hot) > cap:
        rep.note(f"error hotspots: showing top {cap} of {len(hot)} devices failing >= {threshold} policies "
                 f"(broad fleet/device health — likely offline/reimaging lab machines)")

    return rep
