#!/usr/bin/env python3
"""The manifest walk, the linter, the plan and the apply stage, on both
platforms. No tenant, no credentials; PyYAML is the only dependency.

The assertions are the failures that are invisible in production: a condition
that silently would not have been applied, a filtered assignment that reaches
the whole group, an assignment set that has collapsed, and an exclusion that
looks meaningful and subtracts nothing.
"""
from __future__ import annotations

import os
import unittest

from _paths import FIXTURES
from conditions import get_translator
from fake_graph import FakeGraph
from lib.guards import GuardTripped, assignment_floor
from lib.manifests import load_tree, walk_managed_key
from stages import apply_assignments
from stages.lint_conditions import lint_tree
from stages.plan_assignments import build_plan, resolve_exclusions

WIN = get_translator("windows")
MAC = get_translator("macos")
WIN_TREE = FIXTURES / "windows" / "manifests"
MAC_TREE = FIXTURES / "macos" / "manifests"


class ManifestWalk(unittest.TestCase):
    def test_nested_conditions_combine_with_and(self):
        data = {"conditional_items": [{
            "condition": 'hostname BEGINSWITH "LAB"',
            "conditional_items": [{"condition": 'os_version BEGINSWITH "10.0.26"',
                                   "managed_profiles": ["Nested"]}],
        }]}
        (name, cond), = walk_managed_key(data, "managed_profiles")
        self.assertEqual(name, "Nested")
        self.assertEqual(cond, '(hostname BEGINSWITH "LAB") AND (os_version BEGINSWITH "10.0.26")')
        self.assertTrue(WIN.is_supported(cond))

    def test_manifests_outside_usage_roots_are_not_addresses(self):
        groups = {group for _p, group, _d in load_tree(WIN_TREE)}
        self.assertIn("Devices-Assigned-Staff-IT", groups)
        self.assertNotIn(None, groups)
        self.assertEqual(len(groups), 3)


class Linting(unittest.TestCase):
    def test_sample_trees_are_clean(self):
        self.assertEqual(lint_tree(WIN_TREE, WIN), [])
        self.assertEqual(lint_tree(MAC_TREE, MAC), [])

    def test_windows_broken_tree_reports_every_rule(self):
        problems = [f.problem for f in lint_tree(FIXTURES / "windows" / "broken", WIN)]
        joined = " ".join(problems)
        for fragment in ("arch", "form factor", "-endsWith", "ordered comparison",
                         "Malformed", "never read"):
            self.assertIn(fragment, joined)
        self.assertEqual(len(problems), 6)

    def test_macos_broken_tree_reports_every_rule(self):
        problems = " ".join(f.problem for f in lint_tree(FIXTURES / "macos" / "broken", MAC))
        for fragment in ("arch", "marketing name", "-endsWith", "ordered comparison",
                         "Malformed", "never read"):
            self.assertIn(fragment, problems)

    def test_the_same_tree_lints_differently_per_platform(self):
        """machine_type is fine on macOS and a finding on Windows."""
        self.assertEqual(lint_tree(MAC_TREE, MAC), [])
        self.assertTrue(lint_tree(MAC_TREE, WIN))


class Planning(unittest.TestCase):
    def setUp(self):
        os.environ["MIN_DESIRED_ASSIGNMENTS"] = "3"

    def test_plan_resolves_paths_to_groups(self):
        assignments, _excl, skipped = build_plan(WIN_TREE, WIN)
        self.assertEqual(skipped, [])
        pairs = {(a.key, a.name, a.group) for a in assignments}
        self.assertIn(("managed_profiles", "EnableRemoteDesktop", "Devices-Assigned-Staff-IT"), pairs)
        self.assertIn(("managed_apps", "Windows Terminal", "Devices-Assigned-Staff-IT"), pairs)
        self.assertNotIn("NeverAssignedFromHere", {a.name for a in assignments})

    def test_conditional_entries_carry_a_rule(self):
        assignments, _e, _s = build_plan(WIN_TREE, WIN)
        by_name = {a.name: a for a in assignments}
        self.assertIn("osVersion", by_name["ModernOSPrefs"].filter_rule)
        self.assertIn("-notContains", by_name["StaffCleanup"].filter_rule)
        self.assertIsNone(by_name["EnableRemoteDesktop"].filter_rule)

    def test_two_blocks_for_one_item_become_one_or(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "Shared.yaml"
            path.write_text('conditional_items:\n'
                            '- condition: hostname BEGINSWITH "A"\n  managed_profiles: [P]\n'
                            '- condition: hostname BEGINSWITH "B"\n  managed_profiles: [P]\n')
            (a,), _e, _s = build_plan(pathlib.Path(tmp), WIN)
        self.assertEqual(a.filter_rule.count("-startsWith"), 2)
        self.assertIn(" OR ", a.cond)

    def test_unconditional_entry_wins_over_a_condition(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "Shared.yaml").write_text(
                'managed_profiles: [P]\nconditional_items:\n'
                '- condition: hostname BEGINSWITH "A"\n  managed_profiles: [P]\n')
            (a,), _e, _s = build_plan(pathlib.Path(tmp), WIN)
        self.assertIsNone(a.filter_rule)

    def test_exclusion_is_live_only_when_inherited(self):
        assignments, exclusions, _s = build_plan(WIN_TREE, WIN)
        live, inert = resolve_exclusions(assignments, exclusions)
        self.assertIn(("SharedWindowsUpdateRing", "Devices-Shared-Curriculum-Design"), live)
        self.assertTrue(any(name == "OldSecurityAgent" for name, _g in inert))

    def test_broken_tree_skips_rather_than_pretends(self):
        _a, _e, skipped = build_plan(FIXTURES / "windows" / "broken", WIN)
        self.assertTrue(any("never read" in why for _n, _c, why in skipped))
        self.assertTrue(any("-endsWith" in why for _n, _c, why in skipped))

    def test_collapsed_walk_trips_the_floor(self):
        os.environ["MIN_DESIRED_ASSIGNMENTS"] = "500"
        with self.assertRaises(GuardTripped):
            assignment_floor(len(build_plan(WIN_TREE, WIN)[0]))


def tenant(platform: str) -> FakeGraph:
    fake = FakeGraph()
    for group in ("Devices-Shared", "Devices-Shared-Curriculum-Design",
                  "Devices-Assigned-Staff-IT"):
        fake.groups[group] = f"gid-{group}"
    if platform == "windows":
        for name in ("DisableCopilot", "SharedWindowsUpdateRing", "EnableRemoteDesktop"):
            fake.add_object("deviceConfigurations", name)
        fake.add_object("configurationPolicies", "ModernOSPrefs", name_field="name")
        fake.add_object("configurationPolicies", "DellCommandPrefs", name_field="name")
        fake.add_object("deviceHealthScripts", "RepairWinget")
        fake.add_object("deviceHealthScripts", "StaffCleanup")
        fake.add_object("mobileApps", "Windows Terminal")
    else:
        for name in ("SharedSoftwareUpdate", "SharedLoginWindow", "AdminTerminalPrefs",
                     "LaptopPowerPrefs", "DesktopEnergyPrefs"):
            fake.add_object("deviceConfigurations", name)
        fake.add_object("deviceShellScripts", "RepairMunki")
        fake.add_object("deviceShellScripts", "SequoiaCleanup")
        fake.add_object("mobileApps", "Remote Desktop")
    return fake


def assign_body(fake: FakeGraph, endpoint: str, name: str) -> list[dict]:
    obj = next(o for o in fake.objects[endpoint]
               if (o.get("displayName") or o.get("name")) == name)
    for url, body in fake.assigns():
        if f"/{obj['id']}/assign" in url:
            return next(iter(body.values()))
    raise AssertionError(f"{name} was never assigned")


class Applying(unittest.TestCase):
    def setUp(self):
        os.environ["MIN_DESIRED_ASSIGNMENTS"] = "3"
        os.environ["PROTECTED_PROFILES"] = "/nonexistent"

    def run_apply(self, tree, platform, fake):
        return apply_assignments.apply(tree, platform=platform, client=fake)

    def test_filtered_target_carries_a_real_filter_id(self):
        fake = tenant("windows")
        self.assertEqual(self.run_apply(WIN_TREE, "windows", fake), 0)
        (entry,) = assign_body(fake, "configurationPolicies", "ModernOSPrefs")
        target = entry["target"]
        filter_ids = {f["id"] for f in fake.filters}
        self.assertIn(target["deviceAndAppManagementAssignmentFilterId"], filter_ids)
        self.assertEqual(target["deviceAndAppManagementAssignmentFilterType"], "include")
        made = next(f for f in fake.filters
                    if f["id"] == target["deviceAndAppManagementAssignmentFilterId"])
        self.assertEqual(made["platform"], "windows10AndLater")
        self.assertTrue(made["displayName"].startswith("Cimian: "))

    def test_unfiltered_target_has_no_filter_fields(self):
        fake = tenant("windows")
        self.run_apply(WIN_TREE, "windows", fake)
        (entry,) = assign_body(fake, "deviceConfigurations", "EnableRemoteDesktop")
        self.assertNotIn("deviceAndAppManagementAssignmentFilterType", entry["target"])

    def test_remediations_are_never_detect_only(self):
        fake = tenant("windows")
        self.run_apply(WIN_TREE, "windows", fake)
        (entry,) = assign_body(fake, "deviceHealthScripts", "StaffCleanup")
        self.assertTrue(entry["runRemediationScript"])
        self.assertIn("deviceAndAppManagementAssignmentFilterId", entry["target"])

    def test_live_exclusion_is_sent(self):
        fake = tenant("windows")
        self.run_apply(WIN_TREE, "windows", fake)
        body = assign_body(fake, "deviceConfigurations", "SharedWindowsUpdateRing")
        kinds = {(e["target"]["@odata.type"], e["target"]["groupId"]) for e in body}
        self.assertIn(("#microsoft.graph.exclusionGroupAssignmentTarget",
                       "gid-Devices-Shared-Curriculum-Design"), kinds)

    def test_apps_are_required_installs(self):
        fake = tenant("windows")
        self.run_apply(WIN_TREE, "windows", fake)
        (entry,) = assign_body(fake, "mobileApps", "Windows Terminal")
        self.assertEqual(entry["intent"], "required")

    def test_macos_uses_shell_scripts_and_macos_filters(self):
        fake = tenant("macos")
        self.assertEqual(self.run_apply(MAC_TREE, "macos", fake), 0)
        (entry,) = assign_body(fake, "deviceShellScripts", "SequoiaCleanup")
        self.assertIn("deviceAndAppManagementAssignmentFilterId", entry["target"])
        self.assertEqual({f["platform"] for f in fake.filters}, {"macOS"})

    def test_second_run_changes_nothing(self):
        fake = tenant("windows")
        self.run_apply(WIN_TREE, "windows", fake)
        writes = len(fake.posts)
        self.assertEqual(self.run_apply(WIN_TREE, "windows", fake), 0)
        self.assertEqual(len(fake.posts), writes)

    def test_existing_filter_is_reused_and_its_rule_corrected(self):
        fake = tenant("windows")
        fake.filters.append({"id": "filter-old", "platform": "windows10AndLater",
                             "displayName": 'Cimian: os_version BEGINSWITH "10.0.26"',
                             "rule": "(stale)",
                             "description": "Generated from manifest condition: stale"})
        self.run_apply(WIN_TREE, "windows", fake)
        (entry,) = assign_body(fake, "configurationPolicies", "ModernOSPrefs")
        self.assertEqual(entry["target"]["deviceAndAppManagementAssignmentFilterId"], "filter-old")
        self.assertTrue(any(url.endswith("/filter-old") for url, _ in fake.patches))

    def test_hand_made_filter_with_the_same_name_is_never_patched(self):
        """Ownership is the description marker, the same rule target_filter_id
        uses; a matching name alone is not ownership."""
        fake = tenant("windows")
        fake.filters.append({"id": "filter-portal", "platform": "windows10AndLater",
                             "displayName": 'Cimian: os_version BEGINSWITH "10.0.26"',
                             "rule": "(device.osVersion -startsWith \"10.0.2\")",
                             "description": "made by hand in the portal"})
        self.assertEqual(self.run_apply(WIN_TREE, "windows", fake), 1)
        self.assertEqual(fake.patches, [])
        portal = next(f for f in fake.filters if f["id"] == "filter-portal")
        self.assertEqual(portal["description"], "made by hand in the portal")
        obj = next(o for o in fake.objects["configurationPolicies"] if o["name"] == "ModernOSPrefs")
        self.assertFalse(any(f"/{obj['id']}/assign" in url for url, _ in fake.assigns()))

    def test_owned_ids_and_ensure_share_one_rule(self):
        from lib.filters import FilterStore
        fake = tenant("windows")
        fake.filters += [
            {"id": "mine", "platform": "windows10AndLater", "displayName": "Cimian: a",
             "description": "Generated from manifest condition: a"},
            {"id": "theirs", "platform": "windows10AndLater", "displayName": "Cimian: b",
             "description": "portal"},
        ]
        self.assertEqual(FilterStore(fake, WIN).owned_ids(), {"mine"})

    def test_rejected_filter_never_falls_back_to_unfiltered(self):
        fake = tenant("windows")
        fake.reject_filter_rules.add('(device.osVersion -startsWith "10.0.26")')
        self.assertEqual(self.run_apply(WIN_TREE, "windows", fake), 1)
        obj = next(o for o in fake.objects["configurationPolicies"] if o["name"] == "ModernOSPrefs")
        self.assertFalse(any(f"/{obj['id']}/assign" in url for url, _ in fake.assigns()))

    def test_unmarked_objects_are_never_touched(self):
        fake = tenant("windows")
        fake.objects["deviceConfigurations"][0]["description"] = "made in the portal"
        self.run_apply(WIN_TREE, "windows", fake)
        untouched = fake.objects["deviceConfigurations"][0]["id"]
        self.assertFalse(any(f"/{untouched}/assign" in url for url, _ in fake.assigns()))

    def test_broken_tree_applies_nothing(self):
        fake = tenant("windows")
        self.assertEqual(self.run_apply(FIXTURES / "windows" / "broken", "windows", fake), 1)
        self.assertEqual(fake.posts, [])

    def test_what_if_writes_nothing(self):
        fake = tenant("windows")
        fake.what_if = True
        self.assertEqual(self.run_apply(WIN_TREE, "windows", fake), 0)
        self.assertEqual(fake.posts, [])
        self.assertEqual(fake.filters, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
