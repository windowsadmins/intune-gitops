"""Munki conditions -> macOS assignment filters.

What maps:

  hostname       -> device.deviceName
                    ==, !=, CONTAINS, BEGINSWITH.
                    Under NOT: ==, !=, CONTAINS (as -notContains).
                    ENDSWITH does not map: Intune rejects -endsWith on
                    deviceName. NOT BEGINSWITH does not map: Intune rejects
                    -notStartsWith on deviceName.
  machine_type   -> device.model
                    == / != "laptop" / "desktop", and either under NOT.
                    Intune reports device.model as a marketing name ("MacBook
                    Pro", "iMac") when Microsoft's lookup tables know the
                    hardware, and as a bare identifier ("Mac16,3") when they do
                    not. Laptops match the stable "MacBook" prefix; desktop is
                    the exact complement, so every device lands in exactly one
                    branch and new desktop models need no maintenance. The one
                    gap is a laptop that currently reports a bare identifier;
                    list those in a models file (see LAPTOP_MODELS_FILE) and
                    they are added to the laptop branch by exact match.
  os_vers_major  -> device.osVersion
                    == / != on a bare major version, as -startsWith "15." and
                    -notStartsWith "15.". Filters have no ordered comparison, so
                    > < >= <= do not map.

Everything else -- arch, serial_number, catalogs, any custom fact -- raises
UnsupportedCondition with the reason. machine_model is the trap: the filter's
`model` property holds the marketing name, never the identifier Munki reports
("MacBookPro16,1"), so a condition on it would translate cleanly, produce a
valid filter, and match nothing.
"""

from __future__ import annotations

import os
import pathlib

from .common import ORDERED_OPS, Translator, UnsupportedCondition, quote

FILTER_PLATFORM = "macOS"
PREFIX = "Munki: "

FILTER_PROPERTIES = {
    "deviceName",
    "manufacturer",
    "model",
    "osVersion",
    "deviceCategory",
    "enrollmentProfileName",
}

UNTRANSLATABLE = {
    "arch": "no architecture property exists on an assignment filter",
    "serial_number": "serial number is not a filterable property",
    "catalogs": "a client concept Intune has never heard of",
    "munki_version": "a client concept Intune has never heard of",
    "machine_model": "filter `model` is the marketing name, not the hardware identifier",
}

# A YAML file of {models: [{model: "Mac16,3", form_factor: laptop}, ...]}.
# Optional; without it laptops are matched by the MacBook prefix alone.
LAPTOP_MODELS_FILE = os.getenv("INTUNE_LAPTOP_MODELS_FILE", "")

_HOSTNAME = {
    "==": "-eq",
    "!=": "-ne",
    "CONTAINS": "-contains",
    "BEGINSWITH": "-startsWith",
}
_HOSTNAME_NEGATED = {
    "==": "-ne",
    "!=": "-eq",
    "CONTAINS": "-notContains",
}


def is_model_identifier(model: str) -> bool:
    """True for a bare identifier such as "Mac16,3"."""
    if not model.startswith("Mac"):
        return False
    major, sep, minor = model[3:].partition(",")
    return bool(sep) and major.isdigit() and minor.isdigit()


def load_laptop_model_ids(path: str | os.PathLike | None = None) -> set[str]:
    path = path or LAPTOP_MODELS_FILE
    if not path or not pathlib.Path(path).exists():
        return set()
    import yaml

    data = yaml.safe_load(pathlib.Path(path).read_text()) or {}
    ids = set()
    for entry in data.get("models") or []:
        model = (entry.get("model") or "").strip()
        if entry.get("form_factor") == "laptop" and is_model_identifier(model):
            ids.add(model)
    return ids


def machine_type_rules(laptop_ids: set[str]) -> tuple[str, str]:
    """(laptop rule, desktop rule). Desktop uses -notContains "MacBook" because
    Intune rejects -notStartsWith on device.model."""
    laptop = (
        "("
        + " or ".join(
            ['(device.model -startsWith "MacBook")']
            + [f'(device.model -eq "{m}")' for m in sorted(laptop_ids)]
        )
        + ")"
    )
    desktop = (
        "("
        + " and ".join(
            ['(device.model -notContains "MacBook")']
            + [f'(device.model -ne "{m}")' for m in sorted(laptop_ids)]
        )
        + ")"
    )
    return laptop, desktop


def make_predicate(laptop_ids: set[str]):
    laptop_rule, desktop_rule = machine_type_rules(laptop_ids)

    def predicate(field: str, op: str, value: str, kind: str, negate: bool) -> str:
        if field in UNTRANSLATABLE:
            raise UnsupportedCondition(f"{field}: {UNTRANSLATABLE[field]}")

        if field == "hostname":
            ops = _HOSTNAME_NEGATED if negate else _HOSTNAME
            if op in ops:
                return f"(device.deviceName {ops[op]} {quote(value)})"
            if op == "ENDSWITH":
                raise UnsupportedCondition(
                    "hostname ENDSWITH: Intune rejects -endsWith on device.deviceName"
                )
            raise UnsupportedCondition(
                f"{'NOT ' if negate else ''}hostname {op}: no Intune operator for it"
            )

        if field == "machine_type":
            if op not in ("==", "!="):
                raise UnsupportedCondition(f"machine_type {op}: only == and != map")
            is_eq = (op == "==") != negate
            if value.lower() == "laptop":
                return laptop_rule if is_eq else desktop_rule
            if value.lower() == "desktop":
                return desktop_rule if is_eq else laptop_rule
            raise UnsupportedCondition(
                f"machine_type {value!r} is not laptop or desktop"
            )

        if field == "os_vers_major":
            if op in ORDERED_OPS:
                raise UnsupportedCondition(
                    f"os_vers_major {op}: filters have no ordered comparison"
                )
            if op not in ("==", "!="):
                raise UnsupportedCondition(f"os_vers_major {op}: only == and != map")
            if not value.isdigit():
                raise UnsupportedCondition(
                    f"os_vers_major {op} {value!r}: value must be a bare major version"
                )
            if negate:
                op = "!=" if op == "==" else "=="
            if op == "==":
                return f'(device.osVersion -startsWith "{value}.")'
            return f'(device.osVersion -notStartsWith "{value}.")'

        raise UnsupportedCondition(f"{field}: no macOS filter property")

    return predicate


def make_translator(laptop_ids: set[str] | None = None) -> Translator:
    ids = load_laptop_model_ids() if laptop_ids is None else set(laptop_ids)
    return Translator(
        name="macos",
        filter_platform=FILTER_PLATFORM,
        prefix=PREFIX,
        predicate=make_predicate(ids),
    )


TRANSLATOR = make_translator()
translate = TRANSLATOR.translate
