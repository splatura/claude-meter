import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export


def _make_record(
    session_id="session-1",
    plan_tier="max_20x",
    response_timestamp="2026-03-25T10:07:00.000+00:00",
    response_model="claude-opus-4-6",
    request_id="req-abc-123",
    usage=None,
    windows=None,
):
    if usage is None:
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 5,
        }
    if windows is None:
        windows = {
            "5h": {"utilization": 0.25, "status": "allowed"},
        }
    return {
        "id": 1,
        "session_id": session_id,
        "request_id": request_id,
        "declared_plan_tier": plan_tier,
        "response_timestamp": response_timestamp,
        "response_model": response_model,
        "status": 200,
        "usage": usage,
        "ratelimit": {"windows": windows},
    }


def _write_jsonl(records, directory=None):
    """Write records to a JSONL file, optionally inside directory/normalized/."""
    if directory:
        normalized_dir = Path(directory) / "normalized"
        normalized_dir.mkdir(parents=True, exist_ok=True)
        path = normalized_dir / "log.jsonl"
    else:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        path = Path(tmp.name)
        tmp.close()
    with open(path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return path


class TestExportProducesValidSchema(unittest.TestCase):
    def test_export_produces_valid_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            records = [_make_record(), _make_record(session_id="session-2")]
            _write_jsonl(records, directory=tmpdir)

            result = export.export_share(tmpdir)

            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["plan_tier"], "max_20x")
            self.assertIn("exported_at", result)
            self.assertIsInstance(result["records"], list)
            self.assertEqual(len(result["records"]), 2)


class TestExportHashesIds(unittest.TestCase):
    def test_export_hashes_ids(self):
        records = [_make_record(session_id="session-abc", plan_tier="max_20x")]
        path = _write_jsonl(records)

        result = export.export_share(path)
        entry = result["records"][0]

        # Hashes should be 16-char hex strings
        self.assertEqual(len(entry["account_hash"]), 16)
        self.assertEqual(len(entry["session_hash"]), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in entry["account_hash"]))
        self.assertTrue(all(c in "0123456789abcdef" for c in entry["session_hash"]))

        # Hashes should NOT be the raw values
        self.assertNotEqual(entry["account_hash"], "max_20x")
        self.assertNotEqual(entry["session_hash"], "session-abc")

        os.unlink(path)


class TestExportBucketsTimestamps(unittest.TestCase):
    def test_export_buckets_timestamps(self):
        cases = [
            ("2026-03-25T10:07:00.000+00:00", "2026-03-25T10:00:00+00:00"),
            ("2026-03-25T10:16:00.000+00:00", "2026-03-25T10:15:00+00:00"),
            ("2026-03-25T10:30:00.000+00:00", "2026-03-25T10:30:00+00:00"),
            ("2026-03-25T10:44:59.000+00:00", "2026-03-25T10:30:00+00:00"),
            ("2026-03-25T10:00:00.000+00:00", "2026-03-25T10:00:00+00:00"),
        ]
        for input_ts, expected_bucket in cases:
            with self.subTest(input_ts=input_ts):
                records = [_make_record(response_timestamp=input_ts)]
                path = _write_jsonl(records)

                result = export.export_share(path)
                entry = result["records"][0]

                self.assertEqual(entry["timestamp_bucket"], expected_bucket)
                os.unlink(path)


class TestExportExcludesPii(unittest.TestCase):
    def test_export_excludes_pii(self):
        records = [
            _make_record(
                session_id="secret-session-id",
                request_id="secret-request-id",
                plan_tier="max_20x",
            )
        ]
        path = _write_jsonl(records)

        result = export.export_share(path)
        entry = result["records"][0]

        # Serialize record to check no PII leaks
        serialized = json.dumps(entry)
        self.assertNotIn("secret-session-id", serialized)
        self.assertNotIn("secret-request-id", serialized)

        # The records array should not contain raw plan_tier
        # (plan_tier is only in the top-level envelope, not in individual records)
        self.assertNotIn("declared_plan_tier", entry)
        self.assertNotIn("session_id", entry)
        self.assertNotIn("request_id", entry)

        os.unlink(path)


class TestExportHandlesEmptyData(unittest.TestCase):
    def test_export_handles_empty_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty normalized dir
            normalized_dir = Path(tmpdir) / "normalized"
            normalized_dir.mkdir()

            result = export.export_share(tmpdir)

            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["plan_tier"], "")
            self.assertEqual(result["records"], [])

    def test_export_handles_empty_jsonl_file(self):
        path = _write_jsonl([])

        result = export.export_share(path)

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["records"], [])

        os.unlink(path)


class TestExportCliWritesJson(unittest.TestCase):
    def test_export_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            records = [_make_record()]
            _write_jsonl(records, directory=tmpdir)
            output_path = Path(tmpdir) / "share.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "export.py"),
                    tmpdir,
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(output_path.exists())

            with open(output_path) as f:
                data = json.load(f)

            self.assertEqual(data["schema_version"], 1)
            self.assertIsInstance(data["records"], list)
            self.assertEqual(len(data["records"]), 1)


class TestExportSkipsRecordsWithoutUsageOrWindows(unittest.TestCase):
    def test_skips_no_usage(self):
        records = [_make_record(usage={})]
        path = _write_jsonl(records)

        result = export.export_share(path)
        self.assertEqual(result["records"], [])

        os.unlink(path)

    def test_skips_no_windows(self):
        records = [_make_record(windows={})]
        path = _write_jsonl(records)

        result = export.export_share(path)
        self.assertEqual(result["records"], [])

        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
