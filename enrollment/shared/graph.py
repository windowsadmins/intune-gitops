"""A small Microsoft Graph client: paging, retries, group lookup with cache, whatIf.

Deliberately thin. It exists so the consumers and the Intune stages share one
place that knows about paging, throttling and dry-run, not to wrap all of Graph.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)


def _requests():
    """Imported lazily so the test suite -- which never makes a call -- runs
    with no third-party dependencies at all."""
    import requests
    return requests

GRAPH_V1 = "https://graph.microsoft.com/v1.0"
GRAPH_BETA = "https://graph.microsoft.com/beta"

# Pause briefly every N writes. Not superstition: without it a few hundred
# sequential calls reliably start returning 429, leaving an arbitrary prefix of
# the fleet done and no way to tell which.
THROTTLE_EVERY = 20
THROTTLE_SECONDS = 1.0

# Graph answers 429 under load and 502/503/504 when Intune is slow, and either one
# used to fail a whole run. A 429 means Graph refused the request, so every method
# retries it. A 5xx or a dropped connection may already have been applied, so it
# is retried only where a repeat is harmless: reads, PATCH, DELETE and /assign,
# which replaces the whole assignment set. A create POST is never repeated after
# a 5xx, so a retry can never make a duplicate group, filter or membership.
ATTEMPTS = 6
TRANSIENT = (500, 502, 503, 504)
MAX_WAIT = 120


def _repeatable(method: str, url: str) -> bool:
    return method != "POST" or url.split("?")[0].rstrip("/").endswith("/assign")


def _send(method: str, url: str, *, sleep=time.sleep, **kwargs):
    """One Graph call, retried on throttling and transient failures, honouring
    Retry-After. Returns the last response; raises only the last connection
    error, or the first one for a request that must not be repeated."""
    requests = _requests()
    repeatable = _repeatable(method, url)
    delay = 2
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            resp = requests.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as err:
            if last or not repeatable:
                raise
            log.warning("Graph %s %s: %s; retrying in %ds",
                        method, url, err.__class__.__name__, delay)
            sleep(delay)
            delay = min(delay * 2, 60)
            continue
        retry = resp.status_code == 429 or (repeatable and resp.status_code in TRANSIENT)
        if not retry or last:
            return resp
        try:
            wait = int(resp.headers.get("Retry-After") or delay)
        except ValueError:
            wait = delay
        wait = min(max(wait, 1), MAX_WAIT)
        log.warning("Graph %s %s: %d; retrying in %ds", method, url, resp.status_code, wait)
        sleep(wait)
        delay = min(delay * 2, 60)
    return resp


class GraphClient:
    def __init__(self, token: str | None = None, what_if: bool | None = None):
        self.token = token or os.getenv("GRAPH_TOKEN", "")
        if what_if is None:
            what_if = os.getenv("WHATIF", "").strip().lower() == "true"
        self.what_if = what_if
        self._group_ids: dict[str, str | None] = {}
        self._writes = 0
        if not self.token:
            log.warning("No GRAPH_TOKEN set; only whatIf runs will work.")

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def get(self, url: str):
        if not url.startswith("http"):
            url = GRAPH_V1 + url
        return _send("GET", url, headers=self._headers(), timeout=60)

    def paged(self, url: str) -> list[dict]:
        """Follow @odata.nextLink to the end and return every value."""
        items: list[dict] = []
        while url:
            resp = self.get(url)
            resp.raise_for_status()
            body = resp.json()
            items.extend(body.get("value", []))
            url = body.get("@odata.nextLink", "")
        return items

    def post(self, url: str, json: dict | None = None):
        """POST, unless this is a whatIf run, in which case log and skip."""
        if not url.startswith("http"):
            url = GRAPH_V1 + url
        if self.what_if:
            log.info("[WHATIF] POST %s %s", url, json if json else "")
            return None
        self._throttle()
        return _send("POST", url, headers=self._headers(), json=json, timeout=60)

    def patch(self, url: str, json: dict | None = None):
        """PATCH, unless this is a whatIf run."""
        if not url.startswith("http"):
            url = GRAPH_V1 + url
        if self.what_if:
            log.info("[WHATIF] PATCH %s %s", url, json if json else "")
            return None
        self._throttle()
        return _send("PATCH", url, headers=self._headers(), json=json, timeout=60)

    def delete(self, url: str):
        if not url.startswith("http"):
            url = GRAPH_V1 + url
        if self.what_if:
            log.info("[WHATIF] DELETE %s", url)
            return None
        self._throttle()
        return _send("DELETE", url, headers=self._headers(), timeout=60)

    def _throttle(self) -> None:
        self._writes += 1
        if self._writes % THROTTLE_EVERY == 0:
            time.sleep(THROTTLE_SECONDS)

    # -- groups -----------------------------------------------------------

    def group_id(self, name: str) -> str | None:
        """Resolve a group displayName to an id, cached, None if absent."""
        if name in self._group_ids:
            return self._group_ids[name]
        safe = name.replace("'", "''")
        resp = self.get(f"/groups?$filter=displayName eq '{safe}'&$select=id,displayName")
        resp.raise_for_status()
        values = resp.json().get("value", [])
        gid = values[0]["id"] if values else None
        self._group_ids[name] = gid
        return gid

    def ensure_group(self, name: str, description: str) -> str | None:
        """Create the group if it is missing. Empty groups are free, and an
        assignment stage that never has to ask whether its target exists is
        worth a lot of unwritten code."""
        gid = self.group_id(name)
        if gid:
            return gid
        body = {
            "displayName": name,
            "description": description,
            "mailEnabled": False,
            "mailNickname": name.replace("-", "").lower()[:60],
            "securityEnabled": True,
        }
        resp = self.post("/groups", json=body)
        if resp is None:
            log.info("[WHATIF] would create group %s", name)
            return None
        if resp.status_code >= 400:
            log.error("Failed to create group %s: %s %s", name, resp.status_code, resp.text)
            return None
        gid = resp.json()["id"]
        self._group_ids[name] = gid
        log.info("Created group %s", name)
        return gid

    def group_member_ids(self, group_id: str) -> set[str]:
        items = self.paged(f"/groups/{group_id}/members?$select=id&$top=999")
        return {i["id"] for i in items}

    def add_member(self, group_id: str, object_id: str) -> None:
        self.post(
            f"/groups/{group_id}/members/$ref",
            json={"@odata.id": f"{GRAPH_V1}/directoryObjects/{object_id}"},
        )

    def remove_member(self, group_id: str, object_id: str) -> None:
        self.delete(f"/groups/{group_id}/members/{object_id}/$ref")
