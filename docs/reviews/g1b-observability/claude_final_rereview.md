The repo isn't accessible from this environment, so I'll review based on the staged diff content (which is comprehensive and self-contained).

---

**Verdict: APPROVED**

**Critical Issues:**
- None.

**Important Issues:**
- None.

**Minor Issues:**

1. **`JsonlRecords` plain-list degradation.** `summarize_boot_receipts` reads `missing`/`malformed_count` from the container when it's a `JsonlRecords` instance, but falls back to per-record `_malformed_count`/`_missing` when it's a plain `list`. If a caller does `summarize_boot_receipts(list(iter_boot_receipts(path)))` on an **all-malformed** file, the materialized list is empty → no record carries `_malformed_count`, and the fallback `bool(rows[0].get("_missing", False)) if rows else True` defaults `missing=True`. The current tests avoid this by passing `read_boot_receipts(path)` directly; the CLI uses `.tail()` which preserves the subclass. The contract is brittle but the in-tree call sites are correct. (Carries forward the "underscored keys are awkward" concern from `claude_final_review.md`.)

2. **`_candidate_metadata` merge order.** The merge `raw → source_metadata → target_metadata` means if both `source_metadata` and `target_metadata` carry a `source` or `target` key, `target_metadata` wins. The G1A `candidate_receipt()` shape used by the tests apparently puts the relevant `source`/`target` into `source_metadata` only, so the test asserts both correctly. Mildly fragile; an explicit `source = source_meta.get("source")` / `target = target_meta.get("target")` would be more defensible than relying on dict-merge precedence.

3. **`append_once` return type change is API-visible.** `None → bool` is non-breaking for the one in-tree caller at `__init__.py:528` (ignores the return), but any external caller doing `if writer.append_once(...) is None` would silently change behavior. Worth a one-line docstring note that the return is `True` on G1A append success.

4. **Underscore record keys leak.** `_malformed_count` / `_missing` mutate the yielded records to thread file-level metadata. Anything that re-serializes a record (e.g., dumping to JSON for debugging) will leak underscored keys. Acceptable for v1; cleaner solution is to return `(records, malformed, missing)` from `_read_jsonl`. Not blocking.

5. **`append_feedback_event` atomicity assumption.** Single `os.write()` with `O_APPEND` is atomic only when the payload ≤ `PIPE_BUF` (≈4096 bytes on Linux). Current records are small, but future expansion could cross that boundary. A brief comment documenting the single-writer / small-record assumption would future-proof this. Not blocking.

**Required Fixes:**
- None. All three Codex blockers are addressed:
  1. ✓ Real G1A `source_metadata`/`target_metadata` shape is now normalized via `_candidate_metadata`, with tests using `g1a.candidate_receipt()` (`test_boot_receipt_summary_skips_malformed_and_rolls_up_real_g1a_shape`, `test_feedback_events_from_boot_receipt_selected_and_dropped_real_g1a_shape`).
  2. ✓ All-malformed and empty existing files preserve file-level metadata via `JsonlRecords` container (`test_existing_all_malformed_boot_receipts_report_malformed_not_missing`, `test_existing_empty_boot_receipts_report_not_missing`).
  3. ✓ `--limit 0` defined and tested: `max(0, int(raw_limit))` + `tail(0)` returns empty `JsonlRecords` preserving `missing=False` (`test_memory_receipts_limit_truncates_and_zero_means_zero_records`).

**Notes on the eight specific checks:**
1. **Codex blockers fixed** — confirmed, see above.
2. **No ChromaDB writes / remote mutations** — `g1b_observability.py` doesn't import `chromadb`; `on_turn_start` only calls `append_feedback_event`; G1A writer's feedback hook only writes local JSONL. `test_import_has_no_chromadb_side_effect` guards the import surface.
3. **No raw user text in `memory_feedback.jsonl`** — `append_feedback_event` explicitly excludes `context` from `**extra` (`if k not in record and k != "context"`); stores only `context_sha256`. Correction markers carry `label`, integer offsets, and `span_sha256` (hash of the matched marker phrase only).
4. **G1A append first; feedback best-effort** — `append_once` writes the receipt, returns `False` on G1A write failure (skipping G1B), and wraps the G1B append in a separate `try/except` with `debug`-level logging. Boot path cannot crash on feedback failure.
5. **CLI read-only** — `_read_jsonl` short-circuits on `not p.exists()` without touching the path; `cmd_receipts` only reads and prints; `test_memory_receipts_json_absent_file_is_stable` asserts no file creation.
6. **Test coverage** — malformed JSONL ✓, missing files ✓, empty file ✓, all-malformed file ✓, all five marker labels ✓, append-only with partial-line tolerance ✓, provider hook with MEMORY.md/USER.md byte-equality ✓, CLI read-only ✓, `--limit` truncation and `--limit 0` ✓, real G1A candidate shape via `candidate_receipt()` ✓.
7. **No Goal 2 recall behavior** — `recall_needed`/`recall_retrieved`/`recall_used`/`recall_skipped` appear only as accepted strings in `ALLOWED_EVENT_TYPES`; no code path emits them. `G1B_STATUS.md` explicitly lists them as Goal 2 placeholders.
8. **`append_once` return type** — `None → bool` is non-breaking for the in-tree caller (`__init__.py:528` ignores return); new `bool` is consumed only by `test_g1a_receipt_writer_appends_feedback_best_effort`. No `is None` checks elsewhere in the diff's scope. Flagged as minor for any external consumer.

Ship it.
