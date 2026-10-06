"""Cimian conditions -> windows10AndLater assignment filters.

What maps:

  hostname       -> device.deviceName
                    ==, !=, CONTAINS, DOES_NOT_CONTAIN, BEGINSWITH.
                    Under NOT: ==, !=, CONTAINS, DOES_NOT_CONTAIN.
                    ENDSWITH does not map: Intune rejects -endsWith on
                    deviceName. NOT BEGINSWITH does not map: Intune rejects
                    -notStartsWith on deviceName.
  machine_model  -> device.manufacturer OR device.model
                    CONTAINS, DOES_NOT_CONTAIN (and either under NOT).
                    The client reports machine_model as
                    "<Win32_ComputerSystem.Manufacturer> <Model>", so a value
                    like "Dell" or "LENOVO" names the manufacturer, not the
                    model. A substring that spans the join ("Inc. OptiPlex")
                    matches neither property and is not supported.
  os_version     -> device.osVersion
                    == on a three-part "10.0.22631", and BEGINSWITH.
                    The client's fact has no UBR and Intune's does, so ==
                    becomes -startsWith "10.0.22631.". Filters have no ordered
                    comparison, so > < >= <= do not map.

Everything else -- arch, machine_type, catalogs, serial_number, any custom fact
-- raises UnsupportedCondition with the reason. machine_type is the notable
one: Apple's marketing names carry the form factor, so the macOS translator can
map it; Windows model names do not ("Surface Laptop" and "Surface Studio" share
a prefix, and an OEM tower's model says nothing about its shape).
"""

from __future__ import annotations

import re

from .common import ORDERED_OPS, Translator, UnsupportedCondition, quote

FILTER_PLATFORM = "windows10AndLater"
PREFIX = "Cimian: "

# windows10AndLater filters expose these device properties.
FILTER_PROPERTIES = {
    "deviceName",
    "manufacturer",
    "model",
    "osVersion",
    "deviceCategory",
    "enrollmentProfileName",
    "deviceOwnership",
    "operatingSystemSKU",
}

UNTRANSLATABLE = {
    "arch": "no architecture property exists on an assignment filter",
    "serial_number": "serial number is not a filterable property",
    "catalogs": "a client concept Intune has never heard of",
    "machine_type": (
        "Windows model names carry no reliable form factor; set "
        "deviceCategory in Intune and target a group instead"
    ),
    "os_vers_major": (
        "Windows reports osVersion as 10.0.<build>; use os_version "
        '== "10.0.22631" or os_version BEGINSWITH "10.0.26"'
    ),
}

_HOSTNAME = {
    "==": "-eq",
    "!=": "-ne",
    "CONTAINS": "-contains",
    "DOES_NOT_CONTAIN": "-notContains",
    "BEGINSWITH": "-startsWith",
}
_HOSTNAME_NEGATED = {
    "==": "-ne",
    "!=": "-eq",
    "CONTAINS": "-notContains",
    "DOES_NOT_CONTAIN": "-contains",
}


def predicate(field: str, op: str, value: str, _kind: str, negate: bool) -> str:
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

    if field == "machine_model":
        positive = {"CONTAINS": True, "DOES_NOT_CONTAIN": False}.get(op)
        if positive is None:
            raise UnsupportedCondition(
                f"machine_model {op}: only CONTAINS / DOES_NOT_CONTAIN map"
            )
        if negate:
            positive = not positive
        q = quote(value)
        if positive:
            return (
                f"((device.manufacturer -contains {q}) or (device.model -contains {q}))"
            )
        return f"((device.manufacturer -notContains {q}) and (device.model -notContains {q}))"

    if field == "os_version":
        if negate:
            raise UnsupportedCondition(f"NOT os_version {op}: no verified negated form")
        if op in ORDERED_OPS:
            raise UnsupportedCondition(
                f"os_version {op}: filters have no ordered comparison"
            )
        if op == "==" and re.fullmatch(r"\d+\.\d+\.\d+", value):
            return f'(device.osVersion -startsWith "{value}.")'
        if op == "BEGINSWITH":
            return f"(device.osVersion -startsWith {quote(value)})"
        raise UnsupportedCondition(
            f'os_version {op} {value!r}: only == "a.b.c" and BEGINSWITH map'
        )

    raise UnsupportedCondition(f"{field}: no windows10AndLater filter property")


TRANSLATOR = Translator(
    name="windows", filter_platform=FILTER_PLATFORM, prefix=PREFIX, predicate=predicate
)
translate = TRANSLATOR.translate
