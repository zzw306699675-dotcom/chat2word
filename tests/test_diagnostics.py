from __future__ import annotations

import json
from pathlib import Path

import diagnostics as diag


def test_parse_health_events_filters_and_parses_json(tmp_path: Path) -> None:
    log_file = tmp_path / "asr.log"
    log_file.write_text(
        "\n".join(
            [
                "12:00:00 [INFO] session_controller: not health",
                '12:00:01 [INFO] session_controller: HEALTH {"event":"session_started","session_id":1}',
                "12:00:02 [INFO] session_controller: HEALTH invalid-json",
                '12:00:03 [INFO] session_controller: HEALTH {"event":"session_completed","session_id":1}',
            ]
        ),
        encoding="utf-8",
    )

    events = diag.parse_health_events(log_file, limit=10)

    assert len(events) == 2
    assert events[0]["event"] == "session_started"
    assert events[1]["event"] == "session_completed"


def test_summarize_health_events_computes_rates() -> None:
    events = [
        {"event": "session_started"},
        {"event": "session_started"},
        {"event": "session_completed"},
        {"event": "session_failed", "error_code": "NETWORK_ERROR"},
        {"event": "recovery_succeeded", "recover_cost_ms": 120},
        {"event": "recovery_failed"},
    ]
    summary = diag.summarize_health_events(events)

    assert summary["sessions_started"] == 2
    assert summary["sessions_completed"] == 1
    assert summary["sessions_failed"] == 1
    assert summary["session_success_rate"] == 50.0
    assert summary["auto_recover_success_rate"] == 50.0
    assert summary["avg_recover_cost_ms"] == 120
    assert summary["error_code_counts"]["NETWORK_ERROR"] == 1


def test_export_diagnostic_snapshot_writes_json_file(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    log_file = tmp_path / "asr.log"
    log_file.write_text(
        '12:00:01 [INFO] session_controller: HEALTH {"event":"session_started","session_id":1}\n',
        encoding="utf-8",
    )

    class _FakeController:
        def get_health_snapshot(self) -> dict:
            return {"state": "IDLE", "queue_backlog": 0}

    out_dir = tmp_path / "diagnostics"
    monkeypatch.setattr(diag, "default_diagnostics_dir", lambda: out_dir)

    output = diag.export_diagnostic_snapshot(_FakeController(), log_file, reason="test")

    assert output.exists()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["reason"] == "test"
    assert data["controller_health"]["state"] == "IDLE"
    assert data["health_summary"]["sessions_started"] == 1
