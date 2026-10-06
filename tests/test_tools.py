#!/usr/bin/env python3
"""Offline checks for the tenant tools: the parts that decide what gets
written, tested without a tenant."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest

from _paths import ROOT

sys.path.insert(0, str(ROOT / "tools" / "audit"))

from intune_audit import common  # noqa: E402


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reinstall = load(ROOT / "tools" / "reinstall-profile" / "reinstall_profile.py")
autopilot = load(ROOT / "tools" / "autopilot-unlock" / "unlock_autopilot_devices.py")


class Audit(unittest.TestCase):
    def test_split_targets(self):
        split = common.split_targets([
            {"target": {"@odata.type": common.GROUP_TARGET, "groupId": "a"}},
            {"target": {"@odata.type": common.EXCLUDE_TARGET, "groupId": "b"}},
            {"target": {"@odata.type": common.ALL_DEVICES_TARGET}},
        ])
        self.assertEqual(split["include"], {"a"})
        self.assertEqual(split["exclude"], {"b"})
        self.assertTrue(split["all_devices"])
        self.assertFalse(split["all_users"])

    def test_render_filters_by_severity_but_counts_everything(self):
        rep = common.Report("assignments")
        rep.add("high", "orphan", "profile A")
        rep.add("low", "unassigned", "profile B")
        text = common.render([rep], min_severity="high")
        self.assertIn("2 finding(s)", text)
        self.assertIn("profile A", text)
        self.assertNotIn("profile B", text)
        data = json.loads(common.render([rep], as_json=True))
        self.assertEqual(len(data[0]["findings"]), 2)


class Reinstall(unittest.TestCase):
    ORIGINAL = [{"target": {"@odata.type": common.GROUP_TARGET, "groupId": "g1"}}]

    def test_exclusion_is_added_to_the_originals(self):
        body = reinstall.build_assignments(self.ORIGINAL, "temp")
        self.assertEqual(len(body["assignments"]), 2)
        self.assertEqual(body["assignments"][0], self.ORIGINAL[0])
        self.assertEqual(body["assignments"][1]["target"]["@odata.type"],
                         "#microsoft.graph.exclusionGroupAssignmentTarget")

    def test_restore_is_exactly_the_originals(self):
        self.assertEqual(reinstall.build_assignments(self.ORIGINAL),
                         {"assignments": self.ORIGINAL})

    def test_originals_are_not_mutated(self):
        reinstall.build_assignments(self.ORIGINAL, "temp")
        self.assertEqual(len(self.ORIGINAL), 1)


class Autopilot(unittest.TestCase):
    def test_graph_error_is_summarised(self):
        out = 'ERROR: Bad Request({"error":{"code":"BadRequest","message":"nope"}})'
        self.assertEqual(autopilot.AzRestGraphClient._extract_error(out), "BadRequest: nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
