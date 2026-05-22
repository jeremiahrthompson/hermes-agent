# G1B blocker repair after final review

Codex final review returned `REQUEST_CHANGES` on the first final-review pass.

## Active blockers repaired

1. Real G1A candidate metadata shape
   - Problem: G1A boot receipt candidates use `source_metadata` / `target_metadata`, but G1B initially read only `candidate["metadata"]`.
   - Repair: G1B now normalizes both actual G1A receipt candidates and legacy/raw metadata candidates.
   - Tests added/updated:
     - `test_boot_receipt_summary_skips_malformed_and_rolls_up_real_g1a_shape`
     - `test_feedback_events_from_boot_receipt_selected_and_dropped_real_g1a_shape`
     - G1A writer integration fixture now uses `g1a.candidate_receipt()` shape.

2. All-malformed and empty existing JSONL accounting
   - Problem: file-level malformed/missing metadata could be lost when no valid records existed.
   - Repair: new `JsonlRecords` container preserves `malformed_count` and `missing`; materialized records also retain compatibility metadata.
   - Tests added:
     - `test_existing_all_malformed_boot_receipts_report_malformed_not_missing`
     - `test_existing_empty_boot_receipts_report_not_missing`

3. CLI `--limit 0` semantics
   - Problem: `or 100` converted `--limit 0` to the default limit.
   - Repair: `--limit 0` now means summarize zero most-recent records while preserving file-exists/not-missing metadata.
   - Test added:
     - `test_memory_receipts_limit_truncates_and_zero_means_zero_records`

## Verification after repair

Focused G1B suite:

```bash
uv run --with pytest python -m pytest -o addopts='' -q \
  tests/plugins/memory/test_chromadb_g1b_observability.py \
  tests/hermes_cli/test_memory_receipts.py
```

Result: `13 passed`

Expanded memory regression suite:

```bash
uv run --with pytest python -m pytest -o addopts='' -q \
  tests/plugins/memory/test_chromadb_g1b_observability.py \
  tests/hermes_cli/test_memory_receipts.py \
  tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py \
  tests/plugins/memory/test_chromadb_generated_profile.py \
  tests/plugins/memory/test_chromadb_provider.py \
  tests/run_agent/test_memory_prompt_source.py
```

Result: `85 passed`

Compile check:

```bash
uv run python -m py_compile \
  plugins/memory/chromadb/g1b_observability.py \
  plugins/memory/chromadb/g1a.py \
  plugins/memory/chromadb/__init__.py \
  hermes_cli/memory_setup.py \
  hermes_cli/main.py
```

Result: passed
