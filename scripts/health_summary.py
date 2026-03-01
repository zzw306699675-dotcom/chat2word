#!/usr/bin/env python3
"""Summarize ASR health events from log file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is importable when running as:
# python scripts/health_summary.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from diagnostics import parse_health_events, summarize_health_events


def _default_log_file() -> Path:
    return Path.home() / "Library" / "Logs" / "ASR-Assistant.log"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize HEALTH events from ASR log.")
    parser.add_argument(
        "--log-file",
        type=Path,
        default=_default_log_file(),
        help="Path to ASR log file",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum number of health events to parse",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON only",
    )
    args = parser.parse_args()

    events = parse_health_events(args.log_file, limit=args.limit)
    summary = summarize_health_events(events)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"log_file: {args.log_file}")
    print(f"health_events: {summary['health_events']}")
    print(f"sessions_started: {summary['sessions_started']}")
    print(f"sessions_completed: {summary['sessions_completed']}")
    print(f"sessions_failed: {summary['sessions_failed']}")
    print(f"session_success_rate: {summary['session_success_rate']}%")
    print(f"recovery_succeeded: {summary['recovery_succeeded']}")
    print(f"recovery_failed: {summary['recovery_failed']}")
    print(f"auto_recover_success_rate: {summary['auto_recover_success_rate']}%")
    print(f"avg_recover_cost_ms: {summary['avg_recover_cost_ms']}")
    print(f"error_code_counts: {summary['error_code_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
