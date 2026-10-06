"""Dispatcher: python -m intune_audit [domains...] [--json]

Domains: assignments, config, apps, compliance  (or 'all', the default).
Read-only. Run after `az login` as an identity with Intune read access.
"""
import argparse
import sys

from . import common, audit_assignments, audit_config, audit_apps, audit_compliance

MODULES = {
    "assignments": audit_assignments.run,
    "config": audit_config.run,
    "apps": audit_apps.run,
    "compliance": audit_compliance.run,
}


def main():
    ap = argparse.ArgumentParser(prog="intune_audit", description="Read-only Intune audit suite.")
    ap.add_argument("domains", nargs="*",
                    help="modules to run: %s, or 'all' (default)" % ", ".join(MODULES))
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--min-severity", choices=["high", "medium", "low", "info"], default=None,
                    help="only show findings at or above this severity (counts still reflect all)")
    ap.add_argument("--error-threshold", type=int, default=10,
                    help="config: min failed-policy count to flag a device as an error hotspot")
    ap.add_argument("--fail-on-high", action="store_true",
                    help="exit 1 if any high-severity finding (for pipeline gating)")
    args = ap.parse_args()

    chosen = args.domains or ["all"]
    if "all" in chosen:
        chosen = list(MODULES)
    bad = [d for d in chosen if d not in MODULES]
    if bad:
        ap.error("unknown domain(s): %s (valid: %s)" % (", ".join(bad), ", ".join(MODULES)))

    client = common.GraphClient()
    reports = []
    for d in chosen:
        try:
            reports.append(MODULES[d](client, args))
        except Exception as e:
            r = common.Report(d)
            r.note(f"FAILED: {e}")
            reports.append(r)

    print(common.render(reports, as_json=args.json, min_severity=args.min_severity))

    if args.fail_on_high and any(f.severity == "high" for r in reports for f in r.findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
