"""An in-memory stand-in for GraphClient.

Same method names, a dictionary instead of a tenant. Lets the group-ladder
convergence and the assignment stage be tested for the things that actually
frighten us -- a collapsed parse emptying every group, a runaway removal, a
filtered assignment that reaches the whole group -- in under a second, with no
network and no credentials.
"""

from __future__ import annotations

import json as _json


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = _json.dumps(self._body)

    def json(self):
        return self._body


class FakeGraph:
    def __init__(
        self,
        devices: dict[str, str] | None = None,
        what_if: bool = False,
        token: str = "fake-token",
    ):
        # serial -> azureADDeviceId
        self.devices = devices or {}
        self.what_if = what_if
        # Truthy so callers take the write path rather than the offline
        # plan-only path. Set token="" to exercise planning.
        self.token = token
        self.groups: dict[str, str] = {}  # name -> id
        self.members: dict[str, set[str]] = {}  # group id -> object ids
        self.created: list[str] = []
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []
        # Intune side: endpoint name -> list of objects; assignment filters.
        self.objects: dict[str, list[dict]] = {}
        self.filters: list[dict] = []
        self.posts: list[tuple[str, dict]] = []
        self.patches: list[tuple[str, dict]] = []
        self.reject_filter_rules: set[str] = set()
        self._next = 0

    def _id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next}"

    # -- reads -------------------------------------------------------------

    def paged(self, url: str) -> list[dict]:
        if "managedDevices" in url:
            return [
                {
                    "id": f"md-{serial}",
                    "azureADDeviceId": oid,
                    "serialNumber": serial,
                    "managementState": "managed",
                    "enrolledDateTime": "2026-01-01T00:00:00Z",
                }
                for serial, oid in self.devices.items()
            ]
        if "assignmentFilters" in url:
            return [dict(f) for f in self.filters]
        for endpoint, items in self.objects.items():
            if f"/{endpoint}?" in url or url.endswith(f"/{endpoint}"):
                return [dict(i) for i in items]
        return []

    def group_id(self, name: str) -> str | None:
        return self.groups.get(name)

    # -- writes ------------------------------------------------------------

    def post(self, url: str, json: dict | None = None):
        if self.what_if:
            return None
        self.posts.append((url, json or {}))
        if url.endswith("/assignmentFilters"):
            if json["rule"] in self.reject_filter_rules:
                return FakeResponse(400, {"error": {"message": "invalid rule"}})
            created = {**json, "id": self._id("filter")}
            self.filters.append(created)
            return FakeResponse(201, created)
        if url.endswith("/assign"):
            object_id = url.rsplit("/", 2)[-2]
            body_key = next(iter(json))
            for items in self.objects.values():
                for obj in items:
                    if obj["id"] == object_id:
                        obj["assignments"] = [dict(a) for a in json[body_key]]
            return FakeResponse(200, {})
        return FakeResponse(200, {})

    def patch(self, url: str, json: dict | None = None):
        if self.what_if:
            return None
        self.patches.append((url, json or {}))
        fid = url.rsplit("/", 1)[-1]
        for f in self.filters:
            if f["id"] == fid:
                f.update(json or {})
        return FakeResponse(200, {})

    def ensure_group(self, name: str, description: str) -> str:
        if name not in self.groups:
            self.groups[name] = self._id("gid")
            self.members[self.groups[name]] = set()
            self.created.append(name)
        return self.groups[name]

    def group_member_ids(self, group_id: str) -> set[str]:
        return set(self.members.get(group_id, set()))

    def add_member(self, group_id: str, object_id: str) -> None:
        if self.what_if:
            return
        self.members.setdefault(group_id, set()).add(object_id)
        self.added.append((group_id, object_id))

    def remove_member(self, group_id: str, object_id: str) -> None:
        if self.what_if:
            return
        self.members.setdefault(group_id, set()).discard(object_id)
        self.removed.append((group_id, object_id))

    # -- helpers for assertions -------------------------------------------

    def members_of(self, name: str) -> set[str]:
        gid = self.groups.get(name)
        return set(self.members.get(gid, set())) if gid else set()

    def preload(self, name: str, object_ids: set[str]) -> None:
        gid = self.ensure_group(name, "")
        self.members[gid] = set(object_ids)

    def add_object(
        self,
        endpoint: str,
        name: str,
        *,
        name_field: str = "displayName",
        managed: bool = True,
    ) -> dict:
        obj = {
            "id": self._id(endpoint),
            name_field: name,
            "description": "managed-by-gitops" if managed else "",
            "assignments": [],
        }
        self.objects.setdefault(endpoint, []).append(obj)
        return obj

    def assigns(self) -> list[tuple[str, dict]]:
        return [(u, b) for u, b in self.posts if u.endswith("/assign")]
