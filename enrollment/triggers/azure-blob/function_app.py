"""Azure Functions doorbell for the enrollment consumers.

The blob trigger is an Azure detail. The consumer logic is not -- it lives in
../../consumers/ and imports nothing cloud-specific, which is what lets the same
code run behind the plain HTTP handler in ../generic-webhook/.

One consumer per landing blob: no orchestrator, no ordering, no shared state.
The blast radius of a bad deploy is one system. This file wires the `intune`
consumer; add a blob trigger per client consumer you register through
ENROLLMENT_CONSUMERS (see consumers/registry.py).
"""
from __future__ import annotations

import os
import pathlib
import sys

import azure.functions as func

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from consumers import registry  # noqa: E402

CONTAINER = os.getenv("INVENTORY_CONTAINER", "inventory-data/production")
CONSUMERS = registry.load()

app = func.FunctionApp()


@app.blob_trigger(arg_name="blob", path=f"{CONTAINER}/intune.csv",
                  connection="STORAGE_CONNECTION")
def on_intune_csv(blob: func.InputStream) -> None:
    CONSUMERS["intune"](blob.read().decode("utf-8"))
