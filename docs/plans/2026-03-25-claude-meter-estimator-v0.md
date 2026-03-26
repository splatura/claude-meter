# Claude Meter Estimator V0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current noisy per-request cap estimator with a cumulative interval estimator that works on refreshed normalized logs and produces more trustworthy hidden-limit estimates.

**Architecture:** Keep normalization unchanged and improve only the offline analysis layer. The new estimator should group records account-wide by window, accumulate candidate usage units across spans where utilization is flat, and only estimate a cap when utilization visibly changes. It should emit both raw interval data and robust aggregate summaries so we can compare candidate formulas instead of pretending one token sum is definitively correct.

**Tech Stack:** Python 3, JSONL normalized logs, existing `analysis/analyze_normalized_log.py` test harness

## Root-Cause Notes

- The current estimator in [analyze_normalized_log.py](/Users/abhishekray/Projects/opslane/claude-meter/analysis/analyze_normalized_log.py) assumes one request caused one visible utilization jump.
- That is too noisy because:
  - utilization is rounded coarsely, usually in `0.01` jumps
  - many requests happen while utilization appears flat
  - the visible jump may be crossed by the accumulated effect of several earlier requests
  - Anthropic’s hidden accounting may not equal a plain raw token sum
- On the refreshed normalized output, the current estimator produces a wide implied-cap spread, which means the signal is real but the math is too blunt.

## Candidate Usage Meters

For this phase, treat usage formulas as hypotheses:

1. `effective_tokens_raw` (recommended baseline)
   - `input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens`

2. `effective_tokens_no_cache_read`
   - `input_tokens + output_tokens + cache_creation_input_tokens`

3. `effective_tokens_io_only`
   - `input_tokens + output_tokens`

4. `effective_tokens_weighted`
   - a configurable weighted meter, initially:
   - `input + output + cache_creation + (cache_read * weight)`
   - with `weight` exposed as an analysis parameter, default `1.0`

The purpose is not to bless a formula now. The purpose is to see which formula produces the tightest cap distribution.

### Task 1: Add failing tests for interval-based estimation

**Files:**
- Modify: `analysis/test_analyze_normalized_log.py`
- Modify: `analysis/analyze_normalized_log.py`

**Step 1: Write a failing test for cumulative interval building**

Add a test named `test_build_utilization_intervals_accumulates_flat_spans` that feeds a small synthetic record list like:

```python
records = [
    {
        "id": 1,
        "request_timestamp": "2026-03-25T22:00:00Z",
        "status": 200,
        "request_model": "claude-opus-4-6",
        "response_model": "claude-opus-4-6",
        "declared_plan_tier": "max_20x",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "ratelimit": {"windows": {"5h": {"utilization": 0.10}}},
    },
    {
        "id": 2,
        "request_timestamp": "2026-03-25T22:01:00Z",
        "status": 200,
        "request_model": "claude-opus-4-6",
        "response_model": "claude-opus-4-6",
        "declared_plan_tier": "max_20x",
        "usage": {"input_tokens": 200, "output_tokens": 50},
        "ratelimit": {"windows": {"5h": {"utilization": 0.10}}},
    },
    {
        "id": 3,
        "request_timestamp": "2026-03-25T22:02:00Z",
        "status": 200,
        "request_model": "claude-opus-4-6",
        "response_model": "claude-opus-4-6",
        "declared_plan_tier": "max_20x",
        "usage": {"input_tokens": 300, "output_tokens": 50},
        "ratelimit": {"windows": {"5h": {"utilization": 0.11}}},
    },
]
```

Expected behavior:
- the estimator emits one interval for `5h`
- `utilization_before == 0.10`
- `utilization_after == 0.11`
- the interval usage includes records `2` and `3`, not just record `3`
- `delta_utilization == 0.01`

**Step 2: Run the test to verify it fails**

Run: `python3 analysis/test_analyze_normalized_log.py`
Expected: FAIL because interval-based estimation does not exist yet

**Step 3: Write a failing test for account-wide grouping**

Add `test_build_utilization_intervals_ignores_session_boundaries` with two records from different `session_id` values but the same account-level `5h` window.

Expected:
- the interval builder should use timestamp order, not session order
- session changes should not reset interval accumulation for `5h` and `7d`

This reflects the real product model: `5h` and `7d` are account-level windows.

**Step 4: Run the tests to verify they fail**

Run: `python3 analysis/test_analyze_normalized_log.py`
Expected: FAIL because the current analyzer groups by session

### Task 2: Implement candidate usage formulas

**Files:**
- Modify: `analysis/analyze_normalized_log.py`
- Modify: `analysis/test_analyze_normalized_log.py`

**Step 1: Replace `_effective_tokens` with a named meter helper**

Add:

```python
def usage_value(record, meter="effective_tokens_raw", cache_read_weight=1.0):
    ...
```

Support at least:
- `effective_tokens_raw`
- `effective_tokens_no_cache_read`
- `effective_tokens_io_only`
- `effective_tokens_weighted`

**Step 2: Write failing tests for usage-meter variants**

Add tests that assert, for a record with:
- `input_tokens = 10`
- `output_tokens = 5`
- `cache_creation_input_tokens = 20`
- `cache_read_input_tokens = 100`

the helpers return:
- raw: `135`
- no-cache-read: `35`
- io-only: `15`
- weighted with `0.25`: `60`

**Step 3: Run tests to verify they fail**

Run: `python3 analysis/test_analyze_normalized_log.py`
Expected: FAIL because the helper does not exist yet

**Step 4: Implement the helper and re-run tests**

Run: `python3 analysis/test_analyze_normalized_log.py`
Expected: PASS for the meter tests

### Task 3: Implement cumulative interval estimation

**Files:**
- Modify: `analysis/analyze_normalized_log.py`
- Modify: `analysis/test_analyze_normalized_log.py`

**Step 1: Add a new interval builder**

Create a helper like:

```python
def build_utilization_intervals(records, meter="effective_tokens_raw", cache_read_weight=1.0):
    ...
```

Rules:
- eligible records must still be `2xx` and have timestamps
- sort by:
  - `declared_plan_tier`
  - `window`
  - timestamp
  - `id`
- group by:
  - `declared_plan_tier`
  - `window`
- do **not** group by session for account-level windows
- accumulate usage until utilization changes
- when utilization increases, emit one interval

Each interval should include:
- `window`
- `declared_plan_tier`
- `start_id`
- `end_id`
- `start_timestamp`
- `end_timestamp`
- `utilization_before`
- `utilization_after`
- `delta_utilization`
- `record_count`
- `meter`
- `usage_total`
- `implied_cap`
- set of models observed during the interval

**Step 2: Keep the old adjacent-delta output temporarily**

Do not remove `adjacent_deltas` yet. Add a new field like:
- `interval_estimates`

This preserves comparability while we verify the new logic.

**Step 3: Add failing tests for multi-record accumulation**

Add tests showing:
- several flat records should accumulate before the visible jump
- `record_count` on the interval reflects the span
- `usage_total` includes all records in that span

**Step 4: Run the tests to verify they fail, then implement**

Run: `python3 analysis/test_analyze_normalized_log.py`
Expected: PASS after implementation

### Task 4: Add robust summary statistics

**Files:**
- Modify: `analysis/analyze_normalized_log.py`
- Modify: `analysis/test_analyze_normalized_log.py`

**Step 1: Add summary helpers**

Create a helper like:

```python
def summarize_interval_estimates(intervals):
    ...
```

For each `(plan_tier, window, meter)` cohort, compute:
- `count`
- `min`
- `p10`
- `median`
- `p90`
- `max`
- mean if cheap

If there are fewer than 3 points, return only min/median/max to keep it honest.

**Step 2: Add a failing test for summary output**

Construct synthetic intervals with known implied caps:
- `5M`
- `7M`
- `9M`
- `100M` outlier

Expected:
- the summary reports the cohort clearly
- percentiles reflect the distribution
- no fake “single cap” is emitted

**Step 3: Run the test to verify it fails, then implement**

Run: `python3 analysis/test_analyze_normalized_log.py`
Expected: PASS after implementation

### Task 5: Improve CLI/report structure

**Files:**
- Modify: `analysis/analyze_normalized_log.py`

**Step 1: Add CLI options**

Support:

```bash
python3 analysis/analyze_normalized_log.py normalized.jsonl --pretty --meter effective_tokens_raw
python3 analysis/analyze_normalized_log.py normalized.jsonl --pretty --meter effective_tokens_no_cache_read
python3 analysis/analyze_normalized_log.py normalized.jsonl --pretty --meter effective_tokens_weighted --cache-read-weight 0.25
```

Default:
- `meter = effective_tokens_raw`
- `cache_read_weight = 1.0`

**Step 2: Include both old and new sections in output**

The rendered JSON should now include:
- `record_count`
- `window_summary`
- `adjacent_deltas`
- `interval_estimates`
- `interval_summary`
- `meter`
- `cache_read_weight`

This keeps the old view around while making the new one the serious one.

### Task 6: Run the improved estimator on the live normalized logs

**Files:** none

**Step 1: Recombine the live normalized logs**

Run:

```bash
TMP=$(mktemp /tmp/claude-meter-combined.XXXXXX.jsonl)
cat ~/.claude-meter/normalized/*.jsonl > "$TMP"
```

**Step 2: Run multiple meter variants**

Run:

```bash
python3 analysis/analyze_normalized_log.py "$TMP" --pretty --meter effective_tokens_raw
python3 analysis/analyze_normalized_log.py "$TMP" --pretty --meter effective_tokens_no_cache_read
python3 analysis/analyze_normalized_log.py "$TMP" --pretty --meter effective_tokens_weighted --cache-read-weight 0.25
```

**Step 3: Compare results manually**

Look for:
- which meter yields the tightest `interval_summary` spread for `5h`
- whether `7d` intervals are still too sparse to say much
- whether model mix or plan tier needs further cohort splitting

### Task 7: Final verification

**Files:** none

**Step 1: Run the Python analysis tests**

Run:

```bash
python3 analysis/test_analyze_normalized_log.py
python3 analysis/test_normalize_sniffer_log.py
```

Expected: PASS

**Step 2: Run the improved estimator on the live data**

Run at least one full real-data invocation:

```bash
TMP=$(mktemp /tmp/claude-meter-combined.XXXXXX.jsonl)
cat ~/.claude-meter/normalized/*.jsonl > "$TMP"
python3 analysis/analyze_normalized_log.py "$TMP" --pretty --meter effective_tokens_raw
```

Expected:
- `interval_estimates` is non-empty
- `interval_summary` exists
- the cap range is noticeably tighter than the current adjacent-delta output

## Recommended execution order

Implement in this order:
1. usage-meter helpers
2. interval builder
3. summary statistics
4. CLI/report options
5. live-data comparison across meters

That gets us from “the estimator is noisy” to “we can explain which accounting hypothesis best fits the observed utilization changes.”
