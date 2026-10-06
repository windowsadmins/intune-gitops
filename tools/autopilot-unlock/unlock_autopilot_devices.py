"""
Unlock Autopilot MDM-Locked Devices
------------------------------------
Removes Windows Autopilot device registrations from the Intune tenant,
releasing the MDM lock so devices can be reused or returned.

Auth: Uses Azure CLI via `az rest`, so it acts as whoever ran `az login`. That
identity needs DeviceManagementServiceConfig.ReadWrite.All.

Usage:
    python3 unlock_autopilot_devices.py --serials SERIAL001 SERIAL002
    python3 unlock_autopilot_devices.py --csv path/to/devices.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

AUTOPILOT_BASE = "https://graph.microsoft.com/beta/deviceManagement/windowsAutopilotDeviceIdentities"


class GraphApiError(RuntimeError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class AzRestGraphClient:
    def __init__(self):
        if not shutil.which("az"):
            raise RuntimeError("Azure CLI not found on PATH. Install `az` or run from an environment that has it.")

    @staticmethod
    def _extract_error(output: str) -> str:
        text = output.strip()
        if not text:
            return "Graph request failed with no error body"

        def format_body(body: dict) -> str | None:
            error = body.get("error", {})
            message = error.get("message") or body.get("message")
            code = error.get("code") or body.get("code")
            if isinstance(message, str) and message.lstrip().startswith("{"):
                nested = parse_first_json(message)
                if nested:
                    nested_message = nested.get("Message") or nested.get("message")
                    if code and nested_message:
                        return f"{code}: {nested_message}"
                    if nested_message:
                        return str(nested_message)
            if code and message:
                return f"{code}: {message}"
            if message:
                return str(message)
            return None

        def parse_first_json(value: str) -> dict | None:
            decoder = json.JSONDecoder()
            for i, char in enumerate(value):
                if char != "{":
                    continue
                try:
                    body, _ = decoder.raw_decode(value[i:])
                except ValueError:
                    continue
                if isinstance(body, dict):
                    return body
            return None

        decoder = json.JSONDecoder()
        for i, char in enumerate(text):
            if char != "{":
                continue
            try:
                body, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            if not isinstance(body, dict):
                continue
            formatted = format_body(body)
            if formatted:
                return formatted
        return text[:500]

    def request(self, method: str, url: str) -> dict:
        cmd = ["az", "rest", "--method", method, "--url", url, "--output", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = self._extract_error(proc.stderr or proc.stdout)
            hint = ""
            if "Forbidden" in detail or "DeviceManagementServiceConfig" in detail:
                hint = (
                    " Sign in with `az login` as an identity with "
                    "DeviceManagementServiceConfig.ReadWrite.All."
                )
            raise GraphApiError(f"az rest {method.upper()} failed.{hint}", detail)
        if not proc.stdout.strip():
            return {}
        return json.loads(proc.stdout)


class AutopilotDeviceUnlocker:
    def __init__(self):
        self.graph = AzRestGraphClient()
        self.results = []

    def find_autopilot_device_by_serial(self, serial: str) -> dict | None:
        escaped = serial.replace("'", "''")
        query = urllib.parse.urlencode({"$filter": f"contains(serialNumber,'{escaped}')"})
        devices = self.graph.request("GET", f"{AUTOPILOT_BASE}?{query}").get("value", [])
        # Exact match preferred, fall back to first result
        for d in devices:
            if d.get("serialNumber", "").upper() == serial.upper():
                return d
        return devices[0] if devices else None

    def delete_autopilot_device(self, autopilot_id: str) -> tuple[str, str]:
        """
        Returns (status, message).
        status: 'success' | 'in_progress' | 'error'
        """
        url = f"{AUTOPILOT_BASE}/{autopilot_id}"
        try:
            self.graph.request("DELETE", url)
            return "success", "Device removed from Autopilot — MDM lock released"
        except GraphApiError as e:
            if "ZtdDeviceDeletionInProgess" in e.detail:
                return "in_progress", "Deletion already in progress — completes within 30 minutes"
            if "404" in e.detail or "Not Found" in e.detail:
                return "error", "Autopilot device identity not found (may already be deleted)"
            return "error", f"{e}: {e.detail[:200]}"

    def process_device(self, serial: str, equipment_id: str = "") -> dict:
        label = f"[{equipment_id}] " if equipment_id else ""
        print(f"\n{label}Serial: {serial}")

        try:
            device = self.find_autopilot_device_by_serial(serial)
        except GraphApiError as e:
            message = f"{e}: {e.detail[:200]}"
            print(f"  {message}")
            return {
                "equipment_id": equipment_id,
                "serial": serial,
                "status": "error",
                "model": "",
                "enrollment_state": "",
                "autopilot_id": "",
                "message": message,
            }

        if not device:
            print("  Not found in Autopilot registry")
            return {
                "equipment_id": equipment_id,
                "serial": serial,
                "status": "not_found",
                "model": "",
                "enrollment_state": "",
                "autopilot_id": "",
                "message": "Device not found in Autopilot registry",
            }

        model = device.get("model", "Unknown")
        state = device.get("enrollmentState", "unknown")
        autopilot_id = device["id"]
        print(f"  Found: {model} (state: {state})")
        print(f"  Autopilot ID: {autopilot_id}")
        print("  Removing from Autopilot registration...")

        status, message = self.delete_autopilot_device(autopilot_id)
        print(f"  {message}")

        return {
            "equipment_id": equipment_id,
            "serial": serial,
            "status": status,
            "model": model,
            "enrollment_state": state,
            "autopilot_id": autopilot_id,
            "message": message,
        }

    def process_serials(self, serials: list[str]) -> list[dict]:
        print(f"\n{'='*60}")
        print(f"Processing {len(serials)} device(s)")
        print(f"{'='*60}")
        for i, serial in enumerate(serials, 1):
            print(f"\n[{i}/{len(serials)}]", end="")
            result = self.process_device(serial.strip())
            self.results.append(result)
            if i < len(serials):
                time.sleep(1)
        return self.results

    def process_csv(self, csv_path: str) -> list[dict]:
        devices = []
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                serial = row.get("Serial#") or row.get("serial") or row.get("SerialNumber", "")
                eq_id = row.get("Equipment ID") or row.get("equipment_id", "")
                if serial.strip():
                    devices.append((serial.strip(), eq_id.strip()))

        print(f"\n{'='*60}")
        print(f"Processing {len(devices)} device(s) from {csv_path}")
        print(f"{'='*60}")
        for i, (serial, eq_id) in enumerate(devices, 1):
            print(f"\n[{i}/{len(devices)}]", end="")
            result = self.process_device(serial, eq_id)
            self.results.append(result)
            if i < len(devices):
                time.sleep(1)
        return self.results

    def generate_report(self, output_path: str = "AUTOPILOT_UNLOCK_REPORT.md"):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        total = len(self.results)
        success = sum(1 for r in self.results if r["status"] in ("success", "in_progress"))
        not_found = sum(1 for r in self.results if r["status"] == "not_found")
        failed = sum(1 for r in self.results if r["status"] == "error")

        lines = [
            "# Autopilot Unlock Report",
            f"\n**Generated:** {now}",
            f"\n## Summary\n",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total  | {total} |",
            f"| Unlocked / In Progress | {success} |",
            f"| Not Found | {not_found} |",
            f"| Failed | {failed} |",
            f"\n## Device Details\n",
            "| Equipment ID | Serial | Model | Enrollment State | Status | Message |",
            "|---|---|---|---|---|---|",
        ]
        for r in self.results:
            status_icon = {
                "success": "Unlocked",
                "in_progress": "In Progress",
                "not_found": "Not Found",
                "error": "Failed",
            }.get(r["status"], r["status"])
            lines.append(
                f"| {r['equipment_id']} | {r['serial']} | {r['model']} "
                f"| {r['enrollment_state']} | {status_icon} | {r['message']} |"
            )

        report = "\n".join(lines) + "\n"
        Path(output_path).write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {output_path}")
        return report


def main():
    parser = argparse.ArgumentParser(description="Unlock Autopilot MDM-locked devices")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serials", nargs="+", metavar="SERIAL", help="One or more serial numbers")
    group.add_argument("--csv", metavar="FILE", help="CSV file with Serial# column")
    parser.add_argument("--report", default="AUTOPILOT_UNLOCK_REPORT.md", help="Output report path")
    args = parser.parse_args()

    unlocker = AutopilotDeviceUnlocker()

    if args.serials:
        unlocker.process_serials(args.serials)
    else:
        unlocker.process_csv(args.csv)

    total = len(unlocker.results)
    success = sum(1 for r in unlocker.results if r["status"] in ("success", "in_progress"))
    not_found = sum(1 for r in unlocker.results if r["status"] == "not_found")
    failed = sum(1 for r in unlocker.results if r["status"] == "error")

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total: {total} | Unlocked/In-Progress: {success} | Not Found: {not_found} | Failed: {failed}")

    unlocker.generate_report(args.report)


if __name__ == "__main__":
    main()
