#!/usr/bin/env python3
"""The macOS translator against the cases Intune actually rejects."""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import _paths  # noqa: F401
from conditions import UnsupportedCondition, forbidden_operator, get_translator
from conditions import macos

T = get_translator("macos")


def rule(cond: str, translator=T) -> str:
    return translator.translate(cond)[1]


class Supported(unittest.TestCase):
    def test_hostname(self):
        self.assertEqual(rule('hostname BEGINSWITH "LAB"'), '(device.deviceName -startsWith "LAB")')
        self.assertEqual(rule('NOT hostname CONTAINS "KIOSK"'),
                         '(device.deviceName -notContains "KIOSK")')

    def test_machine_type_laptop_and_desktop_are_complements(self):
        laptop = rule('machine_type == "laptop"')
        desktop = rule('machine_type == "desktop"')
        self.assertEqual(laptop, '((device.model -startsWith "MacBook"))')
        self.assertEqual(desktop, '((device.model -notContains "MacBook"))')
        self.assertEqual(rule('NOT machine_type == "laptop"'), desktop)
        self.assertEqual(rule('machine_type != "desktop"'), laptop)
        self.assertIsNone(forbidden_operator(desktop))

    def test_laptops_reporting_a_bare_identifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "models.yaml"
            path.write_text("models:\n- model: Mac16,1\n  form_factor: laptop\n"
                            "- model: Mac16,3\n  form_factor: desktop\n"
                            "- model: MacBook Pro\n  form_factor: laptop\n")
            ids = macos.load_laptop_model_ids(path)
        self.assertEqual(ids, {"Mac16,1"})
        t = macos.make_translator(ids)
        self.assertIn('(device.model -eq "Mac16,1")', rule('machine_type == "laptop"', t))
        self.assertIn('(device.model -ne "Mac16,1")', rule('machine_type == "desktop"', t))

    def test_os_vers_major_equality(self):
        self.assertEqual(rule("os_vers_major == 15"), '(device.osVersion -startsWith "15.")')
        self.assertEqual(rule("NOT os_vers_major == 15"),
                         '(device.osVersion -notStartsWith "15.")')

    def test_display_name_and_platform(self):
        display, _ = T.translate('machine_type == "laptop"')
        self.assertEqual(display, 'Munki: machine_type == "laptop"')
        self.assertEqual(T.filter_platform, "macOS")


class Rejected(unittest.TestCase):
    def test_endswith_is_untranslatable(self):
        with self.assertRaises(UnsupportedCondition):
            rule('hostname ENDSWITH "-LAB"')

    def test_no_ordered_os_comparison(self):
        for cond in ("os_vers_major >= 15", "os_vers_major < 14"):
            with self.subTest(cond=cond):
                with self.assertRaises(UnsupportedCondition):
                    rule(cond)

    def test_machine_model_looks_supported_and_is_not(self):
        with self.assertRaises(UnsupportedCondition) as ctx:
            rule('machine_model == "MacBookPro16,1"')
        self.assertIn("marketing name", str(ctx.exception))

    def test_not_beginswith_is_unsupported(self):
        with self.assertRaises(UnsupportedCondition):
            rule('NOT hostname BEGINSWITH "LAB"')

    def test_unknown_machine_type(self):
        with self.assertRaises(UnsupportedCondition):
            rule('machine_type == "tablet"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
