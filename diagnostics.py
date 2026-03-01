"""Diagnostics helpers for health logs and snapshot export."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

HEALTH_PREFIX = "HEALTH "


def default_diagnostics_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "ASR-Assistant" / "diagnostics"


def parse_health_events(log_file: Path, limit: int = 200) -> list[dict[str, Any]]:
    if limit <= 0 or not log_file.exists():
        return []

    events: list[dict[str, Any]] = []
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                marker = raw_line.find(HEALTH_PREFIX)
                if marker < 0:
                    continue
                payload = raw_line[marker + len(HEALTH_PREFIX):].strip()
                if not payload:
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    events.append(data)
    except OSError:
        return []

    if len(events) > limit:
        return events[-limit:]
    return events


def summarize_health_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    started = 0
    completed = 0
    failed = 0
    recovery_success = 0
    recovery_failed = 0
    recovery_costs: list[int] = []
    error_code_counts: dict[str, int] = {}

    for event in events:
        event_name = str(event.get("event", ""))
        if event_name == "session_started":
            started += 1
        elif event_name == "session_completed":
            completed += 1
        elif event_name == "session_failed":
            failed += 1
            code = str(event.get("error_code", "UNKNOWN"))
            error_code_counts[code] = error_code_counts.get(code, 0) + 1
        elif event_name == "recovery_succeeded":
            recovery_success += 1
            cost = event.get("recover_cost_ms")
            if isinstance(cost, int):
                recovery_costs.append(cost)
        elif event_name == "recovery_failed":
            recovery_failed += 1

    success_rate = (completed / started * 100.0) if started > 0 else 0.0
    total_recovery = recovery_success + recovery_failed
    recovery_success_rate = (
        recovery_success / total_recovery * 100.0 if total_recovery > 0 else 0.0
    )
    avg_recovery_cost_ms = (
        int(sum(recovery_costs) / len(recovery_costs)) if recovery_costs else 0
    )

    return {
        "health_events": len(events),
        "sessions_started": started,
        "sessions_completed": completed,
        "sessions_failed": failed,
        "session_success_rate": round(success_rate, 2),
        "recovery_succeeded": recovery_success,
        "recovery_failed": recovery_failed,
        "auto_recover_success_rate": round(recovery_success_rate, 2),
        "avg_recover_cost_ms": avg_recovery_cost_ms,
        "error_code_counts": error_code_counts,
    }


def export_diagnostic_snapshot(
    controller: Any,
    log_file: Path,
    reason: str = "manual_export",
    max_health_events: int = 200,
) -> Path:
    diagnostics_dir = default_diagnostics_dir()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    health_snapshot: dict[str, Any]
    getter = getattr(controller, "get_health_snapshot", None)
    if getter is None:
        health_snapshot = {}
    else:
        try:
            value = getter()
            health_snapshot = value if isinstance(value, dict) else {}
        except Exception:
            health_snapshot = {}

    health_events = parse_health_events(log_file, limit=max_health_events)
    summary = summarize_health_events(health_events)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "log_file": str(log_file),
        "controller_health": health_snapshot,
        "health_summary": summary,
        "health_events": health_events,
    }

    filename = f"diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path = diagnostics_dir / filename
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
