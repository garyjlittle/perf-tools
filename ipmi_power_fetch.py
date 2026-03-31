#!/usr/bin/env python3
"""
Power reading script - fetches power readings every 2 seconds and logs to CSV.
Uses Redfish API by default (Chassis Power); use --use-ipmitool for ipmitool.
Press Ctrl+C to stop and see summary.
"""

import argparse
import csv
import json
import re
import ssl
import subprocess
import sys
import time
import urllib.request
from base64 import b64encode
from datetime import datetime
from pathlib import Path
from typing import Optional

# Redfish: use PowerControl[0].PowerConsumedWatts (instantaneous) only.
REDFISH_POWER_PATH = "/redfish/v1/Chassis/1/Power"


def get_power_redfish(host: str, username: str, password: str) -> Optional[float]:
    """Fetch chassis power from Redfish Power and return PowerConsumedWatts in Watts."""
    url = f"https://{host}{REDFISH_POWER_PATH}"
    credentials = b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {credentials}"},
    )
    try:
        # HTTPS with no cert verification (same as curl -k)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.load(resp)
    except Exception as e:
        print(f"Error: Redfish request failed: {e}", file=sys.stderr)
        return None

    power_controls = data.get("PowerControl") or []
    if not power_controls:
        print("Error: Redfish Power has no PowerControl array", file=sys.stderr)
        return None

    pc = power_controls[0]
    watts = pc.get("PowerConsumedWatts")
    if watts is not None and isinstance(watts, (int, float)):
        return float(watts)
    return None


def get_power_ipmitool(host: str, username: str, password: str) -> Optional[float]:
    """Run ipmitool dcmi power reading and return instantaneous power in Watts."""
    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", host,
        "-U", username,
        "-P", password,
        "dcmi", "power", "reading",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}", file=sys.stderr)
            return None

        match = re.search(
            r"Instantaneous power reading:\s*(\d+)\s*Watts",
            result.stdout,
            re.IGNORECASE,
        )
        if match:
            return float(match.group(1))
        return None
    except subprocess.TimeoutExpired:
        print("Error: ipmitool timed out", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("Error: ipmitool not found. Install ipmitool.", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch power readings every 2 seconds and log to CSV (Redfish by default)."
    )
    parser.add_argument("-H", "--host", required=True, help="BMC IP address (e.g. 10.2.134.128)")
    parser.add_argument("-U", "--username", required=True, help="BMC/IPMI username")
    parser.add_argument("-P", "--password", required=True, help="BMC/IPMI password")
    parser.add_argument(
        "-o", "--output",
        default="power_readings.csv",
        help="Output CSV file (default: power_readings.csv)",
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=2.0,
        help="Sampling interval in seconds (default: 2)",
    )
    parser.add_argument(
        "--use-ipmitool",
        action="store_true",
        help="Use ipmitool dcmi power reading instead of Redfish API",
    )
    args = parser.parse_args()

    get_power = get_power_ipmitool if args.use_ipmitool else get_power_redfish
    backend = "ipmitool" if args.use_ipmitool else "Redfish"

    csv_path = Path(args.output)
    readings: list[tuple[datetime, float]] = []
    zero_count = 0  # number of times API returned 0 W
    start_time = time.time()

    print(f"Fetching power from {args.host} ({backend}) every {args.interval}s. Press Ctrl+C to stop.\n")
    print(f"Logging to: {csv_path}\n")

    try:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "power_watts"])

            while True:
                power = get_power(args.host, args.username, args.password)
                now = datetime.now()

                if power is not None:
                    if power == 0:
                        zero_count += 1
                        # skip recording and logging zero values
                    else:
                        readings.append((now, power))
                        writer.writerow([now.isoformat(), power])
                        f.flush()
                        elapsed = time.time() - start_time
                        print(f"\r  {now.strftime('%H:%M:%S')}  {power:.0f} W  (elapsed: {elapsed:.0f}s)", end="")

                time.sleep(args.interval)

    except KeyboardInterrupt:
        pass

    # Summary
    elapsed = time.time() - start_time
    print("\n\n--- Summary ---")

    if readings:
        total_watts = sum(p for _, p in readings)
        avg_watts = total_watts / len(readings)
        watt_hours = avg_watts * (elapsed / 3600)

        print(f"Total time measured: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"Readings collected: {len(readings)}")
        print(f"Zero readings (API returned 0 W): {zero_count}")
        print(f"Average power: {avg_watts:.1f} W")
        print(f"Approx. energy consumed: {watt_hours:.2f} Wh")
    else:
        print(f"Total time measured: {elapsed:.1f} seconds")
        print("No readings collected.")

    print(f"\nData saved to: {csv_path}")


if __name__ == "__main__":
    main()
