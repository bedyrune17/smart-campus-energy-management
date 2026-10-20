#!/usr/bin/env python3
"""Local sensor simulator - streams virtual smart-meter readings to the cloud API.

No hardware and no pip packages needed (Python 3.9+ standard library only).

Examples
  # just look at what the virtual sensors produce (no AWS needed)
  python simulator/simulate.py --dry-run --once

  # stream two rooms every 30 s into your deployed API
  python simulator/simulate.py --api https://xxxx.execute-api.ap-south-1.amazonaws.com \
      --key <DeviceApiKey> --rooms AB1-101,LAB-CS1 --interval 30

  # trigger an anomaly on demand (great for demos)
  python simulator/simulate.py --api ... --key ... --rooms AB1-101 --scenario waste --once
  python simulator/simulate.py --api ... --key ... --rooms LAB-CS1 --scenario spike --once

API_URL and DEVICE_API_KEY environment variables work instead of --api / --key.
Tip: if the cloud simulator is also running, stream rooms it does not cover
(e.g. --rooms IOT-LAB-1) or deploy with SIMULATOR_ENABLED=false to avoid double counting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from campus_energy import campus  # noqa: E402
from campus_energy.simulator import SCENARIOS, simulate_reading  # noqa: E402


def post(api: str, key: str, readings: list[dict]) -> dict:
    req = urllib.request.Request(
        api.rstrip("/") + "/readings",
        data=json.dumps({"readings": readings}).encode(),
        headers={"Content-Type": "application/json", "x-device-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode(errors="replace")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=os.environ.get("API_URL"), help="API base URL (stack output ApiUrl)")
    ap.add_argument("--key", default=os.environ.get("DEVICE_API_KEY"), help="device key (stack output DeviceApiKey)")
    ap.add_argument("--rooms", default="all", help="comma-separated room ids, or 'all' (default)")
    ap.add_argument("--interval", type=int, default=60, help="seconds between readings (default 60)")
    ap.add_argument("--scenario", choices=SCENARIOS, help="force an anomaly in the selected rooms")
    ap.add_argument("--once", action="store_true", help="send one round and exit")
    ap.add_argument("--dry-run", action="store_true", help="print readings instead of sending")
    ap.add_argument("--utc-offset-minutes", type=int, default=330, help="campus timezone (default IST)")
    args = ap.parse_args()

    if not args.dry_run and (not args.api or not args.key):
        ap.error("--api and --key are required unless --dry-run (see deploy.sh output)")

    room_ids = list(campus.ROOMS) if args.rooms == "all" else [r.strip() for r in args.rooms.split(",") if r.strip()]
    offset_s = args.utc_offset_minutes * 60
    print(f"Simulating {len(room_ids)} room(s) every {args.interval}s"
          + (f" with scenario '{args.scenario}'" if args.scenario else "")
          + (" (dry run)" if args.dry_run else f" -> {args.api}"))

    while True:
        now = int(time.time())
        rounds = 2 if args.scenario == "waste" else 1  # waste needs consecutive readings to confirm
        for i in range(rounds):
            ts = now - (rounds - 1 - i) * 10
            readings = []
            for rid in room_ids:
                r = simulate_reading(campus.get_room(rid), ts, offset_s, args.interval, args.scenario)
                r["source"] = "local-simulator"
                readings.append(r)
            if args.dry_run:
                for r in readings:
                    print(json.dumps(r))
            else:
                result = post(args.api, args.key, readings)
                total_kw = sum(r["power_w"] for r in readings) / 1000
                print(time.strftime("%H:%M:%S"), f"sent {len(readings)} readings ({total_kw:.1f} kW) ->",
                      json.dumps({k: result.get(k) for k in ("accepted", "alerts", "http_error", "body") if k in result}))
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
