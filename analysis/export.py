#!/usr/bin/env python3
"""Export anonymized share.json from normalized Claude sniffer logs."""

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


def load_records(path):
    """Load JSONL records from a file or directory containing normalized/*.jsonl."""
    p = Path(path)
    if p.is_file():
        yield from _load_jsonl_file(p)
    elif p.is_dir():
        normalized_dir = p / "normalized"
        if normalized_dir.is_dir():
            for jsonl_file in sorted(normalized_dir.glob("*.jsonl")):
                yield from _load_jsonl_file(jsonl_file)
    # else: yield nothing (empty input)


def _load_jsonl_file(path):
    with Path(path).open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _hash16(value):
    """SHA-256 hash of a string, first 16 hex chars."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _bucket_timestamp(iso_timestamp):
    """Round an ISO timestamp DOWN to the nearest 15-minute boundary."""
    if not iso_timestamp:
        return None
    value = iso_timestamp
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    floored_minute = parsed.minute - (parsed.minute % 15)
    bucketed = parsed.replace(minute=floored_minute, second=0, microsecond=0)
    return bucketed.isoformat()


def _has_usage(record):
    usage = record.get("usage")
    if not usage or not isinstance(usage, dict):
        return False
    return any(
        isinstance(usage.get(field), (int, float))
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )


def _has_windows(record):
    windows = ((record.get("ratelimit") or {}).get("windows") or {})
    return len(windows) > 0


def _anonymize_record(record):
    """Produce an anonymized export entry from a normalized record."""
    usage = record.get("usage") or {}
    windows = ((record.get("ratelimit") or {}).get("windows") or {})

    anonymized_windows = {}
    for window_name, window_data in windows.items():
        entry = {}
        if "utilization" in window_data:
            entry["utilization"] = window_data["utilization"]
        if "status" in window_data:
            entry["status"] = window_data["status"]
        anonymized_windows[window_name] = entry

    return {
        "session_hash": _hash16(record.get("session_id") or ""),
        "timestamp_bucket": _bucket_timestamp(
            record.get("response_timestamp") or ""
        ),
        "model": record.get("response_model"),
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        },
        "windows": anonymized_windows,
    }


def export_share(path, output_path=None):
    """Build and optionally write a share.json export."""
    records = list(load_records(path))

    plan_tier = ""
    if records:
        plan_tier = records[0].get("declared_plan_tier") or ""

    anonymized = []
    for record in records:
        if not _has_usage(record):
            continue
        if not _has_windows(record):
            continue
        anonymized.append(_anonymize_record(record))

    result = {
        "schema_version": 1,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "plan_tier": plan_tier,
        "records": anonymized,
    }

    if output_path:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Export anonymized share.json from normalized logs."
    )
    parser.add_argument(
        "log_path",
        help="Path to a normalized JSONL file or directory containing normalized/*.jsonl",
    )
    parser.add_argument(
        "--output",
        default="share.json",
        help="Output file path (default: share.json)",
    )
    args = parser.parse_args()

    result = export_share(args.log_path, output_path=args.output)
    print(
        f"Exported {len(result['records'])} records to {args.output}"
    )


if __name__ == "__main__":
    main()
