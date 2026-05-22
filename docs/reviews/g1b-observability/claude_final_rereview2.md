Verdict: APPROVED

Critical Issues:
- None.

Important Issues:
- None.

Minor Issues:

1. **Documentation/contract is now correct but the `iter_boot_receipts` alias remains a footgun.** `G1B_STATUS.md` now correctly identifies `read_boot_receipts()` / `JsonlRecords` as the metadata-preserving API and warns callers not to materialize a plain list before summarizing zero-valid-record files. The remaining brittleness is that `iter_boot_receipts()` is documented as a "compatibility alias returning the same record container" — a caller seeing the `iter_` prefix may reasonably treat it as a true generator and do `list(iter_boot_receipts(path))`, silently re-triggering the original bug on all-malformed/empty files. The documentation amendment is sufficient for the stated scope, but a future cleanup should either (a) remove the `iter_*` alias, or (b) make it raise/warn when materialized via plain `list()`. Not blocking — the in-tree call sites (`cmd_receipts` uses `.tail()`, tests use `read_boot_receipts` directly) are correct.

2. **`_candidate_metadata` merge order remains implicit.** `raw → source_metadata → target_metadata` with `dict.update` means `target_metadata` wins on key collisions. Real G1A `candidate_receipt()` apparently scopes `source`/`target`/`collection` to `source_metadata` only, so tests pass. An explicit `source = source_meta.get("source")` / `target = target_meta.get("target")` would be more defensible than relying on dict-merge precedence. Carried forward from prior reviews; non-blocking.

3. **`_malformed_count` / `_missing` underscored keys still leak into yielded records.** Any caller that re-serializes a record (e.g., dumping to JSON for debugging) will emit those underscored keys. Cleaner long-term shape: return `(records, malformed, missing)` from `_read_jsonl` and stop mutating records. Acceptable for v1; not blocking.

4. **`append_once` return type change (`None → bool`) is API-visible.** The sole in-tree caller (`__init__.py:528`) ignores the return, so this is non-breaking in-repo. Any external caller doing `if writer.append_once(...) is None` would silently change behavior. A one-line docstring noting `True` on G1A append success would be friendly. Not blocking.

5. **`append_feedback_event` single-`os.write()` atomicity assumption is undocumented.** `O_APPEND` + single `write()` is atomic only when the payload is ≤ `PIPE_BUF` (~4096 bytes on Linux). Current records are small, but future schema expansion could cross the boundary. A brief comment documenting the single-writer / small-record assumption would future-proof this. Not blocking.

Required Fixes:
- None. All prior Codex blockers are addressed:
  1. Real G1A `source_metadata` / `target_metadata` candidate shape normalized via `_candidate_metadata`; tests use `candidate_receipt()`.
  2. All-malformed and empty-existing files preserve file-level metadata via the `JsonlRecords` container returned by `read_boot_receipts()`.
  3. CLI `--limit 0` semantics defined (`max(0, int(...))` + `tail(0)`) and tested; preserves `missing=False`.
  4. Final-review ambiguity resolved by documenting `read_boot_receipts()` / `JsonlRecords` as the metadata-preserving API in `G1B_STATUS.md` and warning against `list()` materialization for zero-valid-record files.

Ship it.
