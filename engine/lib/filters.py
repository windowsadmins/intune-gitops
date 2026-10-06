"""Assignment filters: find or create the filter a condition needs, and build
assignment targets that carry its id.

An assignment target that names a filter type but no filter id is not a
filtered assignment. Intune either rejects it or treats it as unfiltered, and
the second is worse: the item reaches every device in the group instead of the
subset the condition meant. So a target here carries a real
deviceAndAppManagementAssignmentFilterId, or no filter fields at all.

Filters are named "<prefix><normalized condition>" ("Cimian: ...", "Munki:
..."), reused by name, and their rule is rewritten in place when the translator
output changes.

Ownership is one rule, used everywhere: a filter belongs to this pipeline when
its description starts with OWNED_MARKER, which only this module writes. A name
alone is not enough -- anyone can create "Cimian: hostname == ..." by hand in
the portal -- so a filter that matches by name but lacks the marker is never
patched or reused; ensure() raises instead, and the target is not sent.
"""

from __future__ import annotations

import logging

from conditions import (
    Translator,
    UnsupportedCondition,
    forbidden_operator,
    normalize_condition,
)

log = logging.getLogger(__name__)

GRAPH_BETA = "https://graph.microsoft.com/beta"  # assignmentFilters is beta-only
FILTERS_URL = f"{GRAPH_BETA}/deviceManagement/assignmentFilters"

# Intune reports "no filter" as a null id or, on some endpoints, the zero GUID.
NO_FILTER_IDS = {None, "", "00000000-0000-0000-0000-000000000000"}
GROUP_TARGET = "#microsoft.graph.groupAssignmentTarget"
EXCLUDE_TARGET = "#microsoft.graph.exclusionGroupAssignmentTarget"


OWNED_MARKER = "Generated from manifest condition:"


class FilterNotOwned(UnsupportedCondition):
    """A filter with the name this condition needs exists, but was not made here."""


def is_owned(item: dict) -> bool:
    return (item.get("description") or "").startswith(OWNED_MARKER)


class FilterStore:
    """The tenant's filters for one platform, plus a per-run condition cache."""

    def __init__(self, client, translator: Translator):
        self.client = client
        self.translator = translator
        self.by_name: dict[str, dict] = {}
        self._cache: dict[str, str] = {}
        for item in client.paged(FILTERS_URL):
            if item.get("platform") == translator.filter_platform:
                self.by_name[item.get("displayName")] = item

    def owned_ids(self) -> set[str]:
        return {f["id"] for f in self.by_name.values() if is_owned(f) and f.get("id")}

    def ensure(self, condition: str) -> str:
        """Return the filter id for a condition, creating or updating the filter.

        Raises UnsupportedCondition or CondParseError if it cannot be expressed,
        UnsupportedCondition if Intune refuses the write, and FilterNotOwned if a
        filter with the needed name exists but this pipeline did not create it. In a whatIf run a
        filter that does not exist yet comes back as a placeholder id, which is
        only ever logged.
        """
        cond = normalize_condition(condition)
        if cond in self._cache:
            return self._cache[cond]
        display, rule = self.translator.translate(cond)
        bad = forbidden_operator(rule)
        if bad:
            raise UnsupportedCondition(bad)
        desc = f"{OWNED_MARKER} {cond}"

        current = self.by_name.get(display)
        if current and not is_owned(current):
            raise FilterNotOwned(
                f"a filter named {display!r} exists but was not created by this pipeline "
                f"(its description lacks {OWNED_MARKER!r}); rename or delete it, or mark "
                "it as owned, rather than letting the pipeline overwrite it")
        if current:
            if (
                current.get("rule") != rule
                or (current.get("description") or "") != desc
            ):
                resp = self.client.patch(
                    f"{FILTERS_URL}/{current['id']}",
                    json={"displayName": display, "description": desc, "rule": rule},
                )
                if resp is not None and resp.status_code >= 400:
                    raise UnsupportedCondition(
                        f"Intune rejected the filter update for {display!r}: {resp.text[:200]}"
                    )
                current["rule"], current["description"] = rule, desc
            self._cache[cond] = current["id"]
            return current["id"]

        resp = self.client.post(
            FILTERS_URL,
            json={
                "displayName": display,
                "description": desc,
                "platform": self.translator.filter_platform,
                "rule": rule,
                "assignmentFilterManagementType": "devices",
            },
        )
        if resp is None:  # whatIf
            fid = f"whatif:{display}"
        elif resp.status_code >= 400:
            raise UnsupportedCondition(
                f"Intune rejected the filter create for {display!r}: {resp.text[:200]}"
            )
        else:
            created = resp.json()
            self.by_name[display] = created
            fid = created["id"]
            log.info("Created filter %r", display)
        self._cache[cond] = fid
        return fid


def group_target(
    group_id: str, filter_id: str | None = None, exclude: bool = False
) -> dict:
    """An assignment target. With no filter it has no filter fields at all;
    with one it carries the id and the include type together."""
    target = {
        "@odata.type": EXCLUDE_TARGET if exclude else GROUP_TARGET,
        "groupId": group_id,
    }
    if filter_id and not exclude:
        target["deviceAndAppManagementAssignmentFilterId"] = filter_id
        target["deviceAndAppManagementAssignmentFilterType"] = "include"
    return target


def target_filter_id(target: dict, owned_ids: set[str] | None = None) -> str | None:
    """The filter id on an existing target, None when unfiltered.

    With `owned_ids`, a filter this pipeline does not own reads as None, so a
    filter someone added by hand in the portal is not rewritten on every run.
    """
    fid = target.get("deviceAndAppManagementAssignmentFilterId")
    ftype = (target.get("deviceAndAppManagementAssignmentFilterType") or "").lower()
    if fid in NO_FILTER_IDS or ftype == "none":
        return None
    if owned_ids is not None and fid not in owned_ids:
        return None
    return fid
