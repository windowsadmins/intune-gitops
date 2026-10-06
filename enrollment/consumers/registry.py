"""Which consumers a trigger can reach.

This repo ships one consumer, `intune` (the group ladder). Each client repo
adds its own -- cimian-gitops a `cimian` consumer, munki-gitops a `munki` one --
by naming it in ENROLLMENT_CONSUMERS, without editing anything here:

    ENROLLMENT_CONSUMERS="cimian=consumers.cimian:converge"

Several entries are comma-separated. The module must be importable, so put the
client repo's enrollment directory on PYTHONPATH.
"""
from __future__ import annotations

import importlib
import os
from typing import Callable

Converge = Callable[[str], int]


def load(spec: str | None = None) -> dict[str, Converge]:
    from consumers import intune

    found: dict[str, Converge] = {"intune": intune.converge}
    spec = os.getenv("ENROLLMENT_CONSUMERS", "") if spec is None else spec
    for entry in filter(None, (e.strip() for e in spec.split(","))):
        name, _, target = entry.partition("=")
        module, _, attr = target.partition(":")
        if not (name and module):
            raise ValueError(f"bad ENROLLMENT_CONSUMERS entry {entry!r}")
        found[name.strip()] = getattr(importlib.import_module(module.strip()),
                                      (attr or "converge").strip())
    return found
