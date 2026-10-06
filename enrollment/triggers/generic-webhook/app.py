"""A cloud-free doorbell for the same consumers.

    pip install flask
    WEBHOOK_SECRET=... python3 app.py

Send a signed CSV. The signature covers "<timestamp>." followed by the body:

    TS=$(date +%s)
    SIG="sha256=$( { printf '%s.' "$TS"; cat out/intune.csv; } \
          | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | awk '{print $NF}')"
    curl -X POST -H 'Content-Type: text/csv' \
         -H "X-Signature-Timestamp: $TS" -H "X-Signature-256: $SIG" \
         --data-binary @out/intune.csv http://localhost:8080/intune

Requests are refused unless they are text/csv, carry a timestamp within five
minutes, and carry a valid HMAC-SHA256 signature over it and the body; with
WEBHOOK_SECRET unset every request is refused. See auth.py.

Exists to make the point that nothing in consumers/ needs a Functions host. A
cron box, a GitHub Action, or this file are all equally valid front doors.
"""
from __future__ import annotations

import pathlib
import sys

from flask import Flask, request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

import auth  # noqa: E402
from consumers import registry  # noqa: E402


def create_app(consumers=None) -> Flask:
    app = Flask(__name__)
    table = registry.load() if consumers is None else consumers

    @app.post("/<target>")
    def receive(target: str):
        body = request.get_data()
        refused = auth.check(request.headers, body)
        if refused:
            status, reason = refused
            return {"error": reason}, status
        converge = table.get(target)
        if converge is None:
            return {"error": f"unknown target {target}"}, 404
        code = converge(body.decode("utf-8"))
        return {"target": target, "exit": code}, (200 if code == 0 else 500)

    return app


if __name__ == "__main__":
    create_app().run(port=8080)
