"""
Shared framework for the Intune audit suite.

Every audit module imports from here so they share one Graph client, one set of
assignment/group helpers, and one report format. Read-only — nothing here writes
to Intune.

Auth: goes through the `az` CLI (`az rest`), which uses the ambient `az login`.
Sign in as an identity with read access to Intune and the directory (see the
README). Using `az rest` (rather than requests + azure-identity) means the suite
has zero Python dependencies: it runs anywhere `az` and Python 3 do, including a
pipeline agent or a throwaway container, with nothing to pip-install.
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import time
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field

GRAPH = "https://graph.microsoft.com/beta"

# Assignment status enum used across the Intune reporting exports.
STATUS = {"1": "NotApplicable", "2": "Succeeded", "3": "Remediated", "5": "Error", "6": "Conflict"}

# Targets that hit everything — worth flagging when they appear on a real policy.
ALL_DEVICES_TARGET = "#microsoft.graph.allDevicesAssignmentTarget"
ALL_USERS_TARGET = "#microsoft.graph.allLicensedUsersAssignmentTarget"
GROUP_TARGET = "#microsoft.graph.groupAssignmentTarget"
EXCLUDE_TARGET = "#microsoft.graph.exclusionGroupAssignmentTarget"


class GraphClient:
    def __init__(self):
        self._group_name_cache: dict[str, str | None] = {}

    # ---- low level (via `az rest`) -------------------------------------------
    @staticmethod
    def _az(method: str, url: str, body: dict | None = None) -> dict:
        cmd = ["az", "rest", "--method", method, "--url", url]
        if body is not None:
            cmd += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"az rest {method} {url[:90]} failed: {out.stderr.strip()[:300]}")
        return json.loads(out.stdout) if out.stdout.strip() else {}

    def get(self, path: str) -> list[dict]:
        """GET a collection, following @odata.nextLink. `path` may be relative to
        GRAPH or absolute."""
        url = path if path.startswith("http") else f"{GRAPH}/{path}"
        out: list[dict] = []
        while url:
            data = self._az("GET", url)
            out += data.get("value", [])
            url = data.get("@odata.nextLink")
        return out

    def get_one(self, path: str) -> dict:
        url = path if path.startswith("http") else f"{GRAPH}/{path}"
        return self._az("GET", url)

    def post(self, path: str, body: dict) -> dict:
        url = path if path.startswith("http") else f"{GRAPH}/{path}"
        return self._az("POST", url, body)

    # ---- groups --------------------------------------------------------------
    def groups_alive(self, group_ids) -> set[str]:
        """Return the subset of group_ids that still exist (via $batch, 20/req)."""
        ids = sorted(set(group_ids))
        alive: set[str] = set()
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            reqs = [{"id": str(n), "method": "GET", "url": f"/groups/{g}?$select=id"}
                    for n, g in enumerate(chunk)]
            for resp in self.post("$batch", {"requests": reqs}).get("responses", []):
                if resp.get("status") == 200:
                    alive.add(resp["body"]["id"])
        return alive

    def group_name(self, gid: str) -> str | None:
        if gid not in self._group_name_cache:
            try:
                self._group_name_cache[gid] = self.get_one(f"/groups/{gid}?$select=displayName").get("displayName")
            except Exception:
                self._group_name_cache[gid] = None
        return self._group_name_cache[gid]

    def group_member_counts(self, group_ids) -> dict[str, int]:
        """Member count per group via $batch ($count with eventual consistency)."""
        ids = sorted(set(group_ids))
        counts: dict[str, int] = {}
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            reqs = [{"id": str(n), "method": "GET", "url": f"/groups/{g}/members/$count",
                     "headers": {"ConsistencyLevel": "eventual"}} for n, g in enumerate(chunk)]
            for resp in self.post("$batch", {"requests": reqs}).get("responses", []):
                n = int(resp["id"])
                counts[chunk[n]] = resp.get("body", 0) if resp.get("status") == 200 else 0
        return counts

    # ---- reporting exports ---------------------------------------------------
    def export_report(self, report_name: str, fmt: str = "csv", select=None, filt=None,
                      poll_max: int = 120, poll_every: int = 6) -> list[dict]:
        """Run an Intune report export job, download and parse to list[dict].
        Polls up to poll_max*poll_every seconds (default 12m) — these jobs are
        intermittently slow, so give them room."""
        body: dict = {"reportName": report_name, "format": fmt}
        if select:
            body["select"] = select
        if filt:
            body["filter"] = filt
        job = self.post("deviceManagement/reports/exportJobs", body)
        jid, url = job["id"], None
        for _ in range(poll_max):
            j = self.get_one(f"deviceManagement/reports/exportJobs('{jid}')")
            if j.get("status") == "completed":
                url = j.get("url")
                break
            time.sleep(poll_every)
        if not url:
            raise RuntimeError(f"export job '{report_name}' did not complete in time")
        blob = urllib.request.urlopen(url).read()
        zf = zipfile.ZipFile(io.BytesIO(blob))
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        return list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig"))))


def split_targets(assignments) -> dict:
    """Classify an Intune object's assignments. Returns dict with include group
    ids, exclude group ids, and whether it targets all devices / all users."""
    inc, exc = set(), set()
    all_devices = all_users = False
    for a in assignments or []:
        t = a.get("target", {})
        odt = t.get("@odata.type", "")
        if odt == ALL_DEVICES_TARGET:
            all_devices = True
        elif odt == ALL_USERS_TARGET:
            all_users = True
        elif odt.endswith("exclusionGroupAssignmentTarget"):
            if t.get("groupId"):
                exc.add(t["groupId"])
        elif t.get("groupId"):
            inc.add(t["groupId"])
    return {"include": inc, "exclude": exc, "all_devices": all_devices, "all_users": all_users}


def is_hyphen_descendant(child: str, ancestor: str) -> bool:
    return child.startswith(ancestor + "-")


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


@dataclass
class Finding:
    severity: str            # high | medium | low | info
    category: str            # short bucket, e.g. "orphan", "conflict", "broad-target"
    title: str               # the object / subject
    detail: str = ""


@dataclass
class Report:
    domain: str
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, severity, category, title, detail=""):
        self.findings.append(Finding(severity, category, title, detail))

    def note(self, msg):
        self.notes.append(msg)


def _passes(sev: str, min_severity: str | None) -> bool:
    if not min_severity:
        return True
    return SEVERITY_ORDER.get(sev, 9) <= SEVERITY_ORDER.get(min_severity, 9)


def render(reports: list[Report], as_json: bool = False, min_severity: str | None = None) -> str:
    if as_json:
        import json
        return json.dumps([{
            "domain": r.domain,
            "notes": r.notes,
            "findings": [vars(f) for f in r.findings if _passes(f.severity, min_severity)],
        } for r in reports], indent=2)
    out = []
    bar = "=" * 72
    shown = [(r, [f for f in r.findings if _passes(f.severity, min_severity)]) for r in reports]
    counts = defaultdict(int)
    for r in reports:
        for f in r.findings:
            counts[f.severity] += 1
    summary = "  ".join(f"{counts[s]} {s}" for s in ("high", "medium", "low", "info") if counts[s])
    total = sum(counts.values())
    filt = f" (showing >= {min_severity})" if min_severity else ""
    out.append(f"{bar}\nINTUNE AUDIT — {total} finding(s){filt}: {summary or 'none'}\n{bar}")
    for r, findings in shown:
        out.append(f"\n### {r.domain.upper()}  ({len(findings)} shown / {len(r.findings)} total)")
        for n in r.notes:
            out.append(f"  · {n}")
        if not findings:
            out.append("  (clean)" if not r.findings else "  (no findings at this severity)")
            continue
        by_cat = defaultdict(list)
        for f in findings:
            by_cat[f.category].append(f)
        for cat in sorted(by_cat):
            items = sorted(by_cat[cat], key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
            out.append(f"  -- {cat} ({len(items)}) --")
            for f in items:
                d = f"  {f.detail}" if f.detail else ""
                out.append(f"    [{f.severity:6}] {f.title}{d}")
    out.append(bar)
    return "\n".join(out)
