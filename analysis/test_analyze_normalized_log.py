import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_normalized_log as analyzer


class TestAnalyzeNormalizedLog(unittest.TestCase):
    def _write_jsonl(self, records):
        tmp = tempfile.NamedTemporaryFile("w", delete=False)
        with tmp:
            for record in records:
                tmp.write(json.dumps(record) + "\n")
        return Path(tmp.name)

    def test_summarizes_observed_windows(self):
        records = [
            {
                "id": 1,
                "status": 200,
                "response_model": "claude-sonnet-4-6",
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.10},
                        "7d": {"status": "allowed", "utilization": 0.20},
                    }
                },
            },
            {
                "id": 2,
                "status": 429,
                "response_model": "claude-sonnet-4-6",
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "rejected", "utilization": 1.0},
                    }
                },
            },
        ]

        summary = analyzer.summarize_windows(records)

        self.assertEqual(
            summary,
            {
                "5h": {
                    "count": 2,
                    "statuses": {"allowed": 1, "rejected": 1},
                    "min_utilization": 0.1,
                    "max_utilization": 1.0,
                    "models": ["claude-sonnet-4-6"],
                },
                "7d": {
                    "count": 1,
                    "statuses": {"allowed": 1},
                    "min_utilization": 0.2,
                    "max_utilization": 0.2,
                    "models": ["claude-sonnet-4-6"],
                },
            },
        )

    def test_builds_adjacent_window_deltas_for_successful_requests(self):
        records = [
            {
                "id": 1,
                "request_timestamp": "2026-03-25T20:00:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "response_model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "output_tokens": 40,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.10},
                    }
                },
            },
            {
                "id": 2,
                "request_timestamp": "2026-03-25T20:05:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "response_model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 15,
                    "output_tokens": 20,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.15},
                    }
                },
            },
            {
                "id": 3,
                "request_timestamp": "2026-03-25T20:06:00.000+00:00",
                "session_id": "session-2",
                "status": 200,
                "response_model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 7,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.17},
                    }
                },
            },
        ]

        deltas = analyzer.build_adjacent_deltas(records)

        self.assertEqual(
            deltas,
            [
                {
                    "session_id": "session-1",
                    "window": "5h",
                    "previous_id": 1,
                    "current_id": 2,
                    "previous_timestamp": "2026-03-25T20:00:00.000+00:00",
                    "current_timestamp": "2026-03-25T20:05:00.000+00:00",
                    "response_model": "claude-sonnet-4-6",
                    "utilization_before": 0.10,
                    "utilization_after": 0.15,
                    "delta_utilization": 0.05,
                    "effective_tokens": 50,
                    "implied_cap_tokens": 1000.0,
                }
            ],
        )

    def test_usage_value_supports_candidate_meters(self):
        record = {
            "response_model": "claude-opus-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 100,
                "output_tokens": 5,
            }
        }

        self.assertEqual(analyzer.usage_value(record, meter="effective_tokens_raw"), 135)
        self.assertEqual(
            analyzer.usage_value(record, meter="effective_tokens_no_cache_read"),
            35,
        )
        self.assertEqual(analyzer.usage_value(record, meter="effective_tokens_io_only"), 15)
        self.assertEqual(
            analyzer.usage_value(record, meter="effective_tokens_weighted", cache_read_weight=0.25),
            60,
        )
        self.assertEqual(
            analyzer.usage_value(record, meter="price_equivalent_5m"),
            350.0,
        )

    def test_build_utilization_intervals_sorts_by_response_timestamp_and_window(self):
        records = [
            {
                "id": 1,
                "request_timestamp": "2026-03-25T22:00:00.000+00:00",
                "response_timestamp": "2026-03-25T22:30:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 100, "output_tokens": 0},
                "ratelimit": {
                    "windows": {
                        "7d": {"status": "allowed", "utilization": 0.10},
                        "5h": {"status": "allowed", "utilization": 0.10},
                    }
                },
            },
            {
                "id": 2,
                "request_timestamp": "2026-03-25T22:05:00.000+00:00",
                "response_timestamp": "2026-03-25T22:06:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 200, "output_tokens": 0},
                "ratelimit": {
                    "windows": {
                        "7d": {"status": "allowed", "utilization": 0.10},
                        "5h": {"status": "allowed", "utilization": 0.10},
                    }
                },
            },
            {
                "id": 3,
                "request_timestamp": "2026-03-25T22:10:00.000+00:00",
                "response_timestamp": "2026-03-25T22:40:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 300, "output_tokens": 0},
                "ratelimit": {
                    "windows": {
                        "7d": {"status": "allowed", "utilization": 0.11},
                        "5h": {"status": "allowed", "utilization": 0.11},
                    }
                },
            },
        ]

        intervals = analyzer.build_utilization_intervals(records)

        self.assertEqual(
            [
                (interval["window"], interval["start_id"], interval["end_id"])
                for interval in intervals
            ],
            [("5h", 1, 3), ("7d", 1, 3)],
        )

    def test_build_utilization_intervals_accumulates_flat_spans(self):
        records = [
            {
                "id": 1,
                "request_timestamp": "2026-03-25T22:00:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 50,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.10},
                    }
                },
            },
            {
                "id": 2,
                "request_timestamp": "2026-03-25T22:01:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {
                    "input_tokens": 200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 50,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.10},
                    }
                },
            },
            {
                "id": 3,
                "request_timestamp": "2026-03-25T22:02:00.000+00:00",
                "session_id": "session-2",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {
                    "input_tokens": 300,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 50,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.11},
                    }
                },
            },
        ]

        intervals = analyzer.build_utilization_intervals(records)

        self.assertEqual(len(intervals), 1)
        self.assertEqual(
            intervals[0],
            {
                "account_fingerprint": "unknown",
                "declared_plan_tier": "max_20x",
                "window": "5h",
                "start_id": 2,
                "end_id": 3,
                "start_timestamp": "2026-03-25T22:01:00.000+00:00",
                "end_timestamp": "2026-03-25T22:02:00.000+00:00",
                "utilization_before": 0.10,
                "utilization_after": 0.11,
                "delta_utilization": 0.01,
                "record_count": 2,
                "meter": "effective_tokens_raw",
                "complete_usage": True,
                "usage_total": 600,
                "implied_cap": 60000.0,
                "models": ["claude-opus-4-6"],
            },
        )

    def test_build_utilization_intervals_ignores_session_boundaries_for_both_windows(self):
        records = [
            {
                "id": 10,
                "request_timestamp": "2026-03-25T22:00:00.000+00:00",
                "session_id": "session-a",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "ratelimit": {
                    "windows": {
                        "5h": {"utilization": 0.10},
                        "7d": {"utilization": 0.20},
                    }
                },
            },
            {
                "id": 11,
                "request_timestamp": "2026-03-25T22:01:00.000+00:00",
                "session_id": "session-b",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 20, "output_tokens": 0},
                "ratelimit": {
                    "windows": {
                        "5h": {"utilization": 0.10},
                        "7d": {"utilization": 0.20},
                    }
                },
            },
            {
                "id": 12,
                "request_timestamp": "2026-03-25T22:02:00.000+00:00",
                "session_id": "session-a",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 30, "output_tokens": 0},
                "ratelimit": {
                    "windows": {
                        "5h": {"utilization": 0.11},
                        "7d": {"utilization": 0.21},
                    }
                },
            },
        ]

        intervals = analyzer.build_utilization_intervals(records)

        self.assertEqual(
            [(interval["window"], interval["start_id"], interval["end_id"]) for interval in intervals],
            [("5h", 11, 12), ("7d", 11, 12)],
        )

    def test_build_utilization_intervals_marks_incomplete_usage(self):
        records = [
            {
                "id": 1,
                "request_timestamp": "2026-03-25T22:00:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "account_fingerprint": "acct-123",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "ratelimit": {"windows": {"5h": {"utilization": 0.10}}},
            },
            {
                "id": 2,
                "request_timestamp": "2026-03-25T22:01:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "account_fingerprint": "acct-123",
                "response_model": "claude-opus-4-6",
                "usage": {},
                "ratelimit": {"windows": {"5h": {"utilization": 0.10}}},
            },
            {
                "id": 3,
                "request_timestamp": "2026-03-25T22:02:00.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "account_fingerprint": "acct-123",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 30, "output_tokens": 0},
                "ratelimit": {"windows": {"5h": {"utilization": 0.11}}},
            },
        ]

        intervals = analyzer.build_utilization_intervals(records)

        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["account_fingerprint"], "acct-123")
        self.assertFalse(intervals[0]["complete_usage"])
        self.assertIsNone(intervals[0]["usage_total"])
        self.assertIsNone(intervals[0]["implied_cap"])

    def test_build_utilization_intervals_groups_by_account_fingerprint(self):
        records = [
            {
                "id": 1,
                "request_timestamp": "2026-03-25T22:00:00.000+00:00",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "account_fingerprint": "acct-a",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "ratelimit": {"windows": {"5h": {"utilization": 0.10}}},
            },
            {
                "id": 2,
                "request_timestamp": "2026-03-25T22:01:00.000+00:00",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "account_fingerprint": "acct-b",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 20, "output_tokens": 0},
                "ratelimit": {"windows": {"5h": {"utilization": 0.10}}},
            },
            {
                "id": 3,
                "request_timestamp": "2026-03-25T22:02:00.000+00:00",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "account_fingerprint": "acct-a",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 30, "output_tokens": 0},
                "ratelimit": {"windows": {"5h": {"utilization": 0.11}}},
            },
            {
                "id": 4,
                "request_timestamp": "2026-03-25T22:03:00.000+00:00",
                "status": 200,
                "declared_plan_tier": "max_20x",
                "account_fingerprint": "acct-b",
                "response_model": "claude-opus-4-6",
                "usage": {"input_tokens": 40, "output_tokens": 0},
                "ratelimit": {"windows": {"5h": {"utilization": 0.11}}},
            },
        ]

        intervals = analyzer.build_utilization_intervals(records)

        self.assertEqual(
            [(interval["account_fingerprint"], interval["start_id"], interval["end_id"]) for interval in intervals],
            [("acct-a", 3, 3), ("acct-b", 4, 4)],
        )

    def test_cli_outputs_summary_json(self):
        log_path = self._write_jsonl(
            [
                {
                    "id": 1,
                    "request_timestamp": "2026-03-25T20:00:00.000+00:00",
                    "session_id": "session-1",
                    "status": 200,
                    "response_model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 10,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 0,
                    },
                    "ratelimit": {
                        "windows": {
                            "5h": {"status": "allowed", "utilization": 0.10},
                        }
                    },
                }
            ]
        )

        output = analyzer.render_analysis(log_path)
        parsed = json.loads(output)

        self.assertEqual(parsed["record_count"], 1)
        self.assertIn("5h", parsed["window_summary"])
        self.assertIn("interval_estimates", parsed)
        self.assertIn("adjacent_deltas", parsed)
        self.assertIn("meter_comparison", parsed)

    def test_builds_5h_meter_comparison_for_raw_and_price_equivalent(self):
        records = [
            {
                "id": 1,
                "request_timestamp": "2026-03-25T20:00:00.000+00:00",
                "response_timestamp": "2026-03-25T20:00:01.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "response_model": "claude-opus-4-6",
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 5,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.10},
                    }
                },
            },
            {
                "id": 2,
                "request_timestamp": "2026-03-25T20:05:00.000+00:00",
                "response_timestamp": "2026-03-25T20:05:01.000+00:00",
                "session_id": "session-1",
                "status": 200,
                "response_model": "claude-opus-4-6",
                "usage": {
                    "input_tokens": 4,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 10,
                    "output_tokens": 2,
                },
                "ratelimit": {
                    "windows": {
                        "5h": {"status": "allowed", "utilization": 0.12},
                    }
                },
            },
        ]

        comparison = analyzer.build_meter_comparison(records)

        self.assertEqual(
            comparison,
            {
                "5h": {
                    "effective_tokens_raw": {
                        "count": 1,
                        "min": 1200.0,
                        "median": 1200.0,
                        "max": 1200.0,
                    },
                    "price_equivalent_5m": {
                        "count": 1,
                        "min": 6250.0,
                        "median": 6250.0,
                        "max": 6250.0,
                    },
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
