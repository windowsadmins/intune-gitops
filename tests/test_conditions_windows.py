#!/usr/bin/env python3
"""The Windows translator against the cases Intune actually rejects."""
from __future__ import annotations

import unittest

import _paths  # noqa: F401
from conditions import CondParseError, UnsupportedCondition, forbidden_operator, get_translator

T = get_translator("windows")


def rule(cond: str) -> str:
    return T.translate(cond)[1]


class Supported(unittest.TestCase):
    def test_hostname_operators(self):
        self.assertEqual(rule('hostname == "LAB-01"'), '(device.deviceName -eq "LAB-01")')
        self.assertEqual(rule('hostname BEGINSWITH "LAB"'), '(device.deviceName -startsWith "LAB")')
        self.assertEqual(rule('hostname CONTAINS "STUDIO"'), '(device.deviceName -contains "STUDIO")')

    def test_not_contains_is_allowed(self):
        """Production uses -notContains; an earlier linter wrongly rejected it."""
        r = rule('hostname DOES_NOT_CONTAIN "KIOSK"')
        self.assertEqual(r, '(device.deviceName -notContains "KIOSK")')
        self.assertIsNone(forbidden_operator(r))

    def test_machine_model_maps_to_manufacturer_or_model(self):
        self.assertEqual(
            rule('machine_model CONTAINS "Dell"'),
            '((device.manufacturer -contains "Dell") or (device.model -contains "Dell"))')
        self.assertEqual(
            rule('machine_model DOES_NOT_CONTAIN "Dell"'),
            '((device.manufacturer -notContains "Dell") and (device.model -notContains "Dell"))')

    def test_machine_model_equality_does_not_map(self):
        with self.assertRaises(UnsupportedCondition):
            rule('machine_model == "Dell Inc. OptiPlex 7090"')

    def test_os_version_exact_build_gains_a_ubr_wildcard(self):
        self.assertEqual(rule('os_version == "10.0.22631"'),
                         '(device.osVersion -startsWith "10.0.22631.")')
        self.assertEqual(rule('os_version BEGINSWITH "10.0.26"'),
                         '(device.osVersion -startsWith "10.0.26")')

    def test_display_name_is_prefixed_and_normalised(self):
        display, _ = T.translate('hostname   ==  "A"')
        self.assertEqual(display, 'Cimian: hostname == "A"')
        self.assertEqual(T.filter_platform, "windows10AndLater")


class NotPushdown(unittest.TestCase):
    def test_not_never_emits_unary_not(self):
        r = rule('NOT hostname CONTAINS "KIOSK"')
        self.assertEqual(r, '(device.deviceName -notContains "KIOSK")')
        self.assertNotIn("(not ", r)

    def test_de_morgan_over_and_or(self):
        r = rule('NOT (hostname == "A" OR machine_model CONTAINS "HP")')
        self.assertEqual(r, '((device.deviceName -ne "A") and '
                            '((device.manufacturer -notContains "HP") and '
                            '(device.model -notContains "HP")))')
        self.assertIsNone(forbidden_operator(r))

    def test_double_negation_cancels(self):
        self.assertEqual(rule('NOT NOT hostname == "A"'), '(device.deviceName -eq "A")')

    def test_not_beginswith_is_unsupported(self):
        """Intune rejects -notStartsWith on deviceName."""
        with self.assertRaises(UnsupportedCondition):
            rule('NOT hostname BEGINSWITH "LAB"')

    def test_not_os_version_is_unsupported(self):
        with self.assertRaises(UnsupportedCondition):
            rule('NOT os_version BEGINSWITH "10.0.26"')


class Rejected(unittest.TestCase):
    def test_endswith_is_untranslatable(self):
        with self.assertRaises(UnsupportedCondition) as ctx:
            rule('hostname ENDSWITH "-LAB"')
        self.assertIn("-endsWith", str(ctx.exception))

    def test_no_ordered_os_version_comparison(self):
        """Windows 11 reports 10.0.22xxx; '>= 11' was never a working filter."""
        for cond in ('os_version >= "10.0.22000"', 'os_version < "10.0.26100"',
                     "os_vers_major >= 11"):
            with self.subTest(cond=cond):
                with self.assertRaises(UnsupportedCondition):
                    rule(cond)

    def test_facts_with_no_filter_property(self):
        for cond in ('arch == "arm64"', 'machine_type == "laptop"',
                     'catalogs CONTAINS "Testing"', 'serial_number == "X1"',
                     'device_category == "Laptop"'):
            with self.subTest(cond=cond):
                with self.assertRaises(UnsupportedCondition):
                    rule(cond)

    def test_malformed(self):
        for cond in ["os_version ==", '(hostname == "A"', "", 'hostname == "A" &&']:
            with self.subTest(cond=cond):
                with self.assertRaises(CondParseError):
                    rule(cond)

    def test_unsupported_and_malformed_are_different_failures(self):
        self.assertFalse(issubclass(UnsupportedCondition, CondParseError))
        self.assertFalse(issubclass(CondParseError, UnsupportedCondition))

    def test_guard_catches_a_regression(self):
        self.assertIsNotNone(forbidden_operator('(device.deviceName -endsWith "X")'))
        self.assertIsNotNone(forbidden_operator('(not (device.model -eq "X"))'))
        self.assertIsNotNone(forbidden_operator('(device.manufacturer -notStartsWith "X")'))


if __name__ == "__main__":
    unittest.main(verbosity=2)
