import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard  # noqa: E402


def _make_record(record_id, timestamp, utilization_5h=0.10, input_tokens=100, output_tokens=50):
    return {
        "id": record_id,
        "request_timestamp": timestamp,
        "response_timestamp": timestamp,
        "session_id": "session-1",
        "status": 200,
        "declared_plan_tier": "max_20x",
        "account_fingerprint": "acct-1",
        "response_model": "claude-opus-4-6",
        "usage": {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": output_tokens,
        },
        "ratelimit": {
            "windows": {
                "5h": {"status": "allowed", "utilization": utilization_5h},
                "7d": {"status": "allowed", "utilization": utilization_5h * 0.5},
            }
        },
    }


def _sample_records():
    return [
        _make_record(1, "2026-03-25T20:00:00.000+00:00", utilization_5h=0.10),
        _make_record(2, "2026-03-25T20:01:00.000+00:00", utilization_5h=0.15),
        _make_record(3, "2026-03-25T20:02:00.000+00:00", utilization_5h=0.20),
    ]


def test_empty_data_returns_no_data_html():
    html = dashboard._generate_no_data_html()
    assert "No data yet" in html
    assert "<!DOCTYPE html>" in html
    assert "viewport" in html


def test_output_structure_with_sample_records():
    records = _sample_records()
    data = dashboard._build_dashboard_data(records)
    html = dashboard._generate_html(data)

    assert "<canvas" in html
    assert "const DATA =" in html
    assert "chart.js@4.4.7" in html
    assert "viewport" in html
    assert "claude-meter" in html


def test_output_flag_writes_to_path(tmp_path):
    records = _sample_records()
    data = dashboard._build_dashboard_data(records)
    html = dashboard._generate_html(data)

    out_file = tmp_path / "dashboard.html"
    out_file.write_text(html)

    assert out_file.exists()
    content = out_file.read_text()
    assert "<canvas" in content
    assert len(content) > 1000
