Verification complete. The diff applies cleanly against post-G1A `main`, the provider's `_hermes_home`/`_platform`/`_gateway_session_key`/`_session_id` are all initialized in `__init__` (so the `on_turn_start` hook is safe at any point), the only caller of `append_once` (`__init__.py:528`) ignores the return value (so the `None → bool` change is non-breaking), and the manager's `on_turn_start` already wraps provider hooks in try/except (so the in-method `try/except` is belt-and-suspenders).

---

**Verdict: APPROVED**

**Critical Issues:**
- None.

**Important Issues:**
- None.

**Minor Issues:**
1. `iter_boot_receipts` / `iter_feedback_events` mutate each yielded record with `_malformed_count` and `_missing` underscored keys to thread file-level metadata through the generator. It works because `summarize_*` reads them off `rows[0]`, but it conflates record-level and file-level state, and any caller that stores or re-serializes a yielded record will leak those underscored keys. Returning `(records, malformed, missing)` from a single helper, or exposing a small `read_jsonl()` that returns the tuple, would be cleaner — not a blocker.
2. `cmd_receipts` human output omits `selected_ids`, `dropped_ids`, `drop_reasons`, `fallback_reasons`, `fallback_rate`, and `output_hashes`. They're available via `--json`, but the human path is arguably under-informative for the stated "salience tuning evidence" goal. Acceptable for v1.
3. `append_feedback_event` writes with `os.O_APPEND` + a single `os.write()`, which is atomic only when the payload is ≤ `PIPE_BUF` (typically 4096 bytes on Linux). Records here are small, but a future expansion (e.g., embedding more candidate metadata) could exceed that. Document the single-writer / small-record assumption, or leave a note — not blocking.
4. The `gateway_session_key` plumbing through `kwargs.get("gateway_session_key", self._gateway_session_key)` in `on_turn_start` works, but `MemoryManager.on_turn_start` does not currently forward `gateway_session_key` in its documented kwargs (only `remaining_tokens, model, platform, tool_count`). The fallback to `self._gateway_session_key` handles it correctly; just be aware that in practice the instance attr is the source of truth.

**Required Fixes:**
- None.

**Notes on the seven specific checks:**
1. **No ChromaDB writes / remote service mutations by G1B** — confirmed. `g1b_observability.py` does not import `chromadb` or any provider client; the provider hook explicitly avoids the vector store, and the test `test_import_has_no_chromadb_side_effect` guards against accidental imports.
2. **No raw user text in `memory_feedback.jsonl`** — confirmed. `append_feedback_event` stores `context_sha256` only (never a `context` string — explicitly filtered in the `**extra` loop), and the correction-marker path passes `span_sha256` (hash of the matched marker phrase, not the surrounding utterance). `marker_start`/`marker_end` are integer offsets only.
3. **G1A receipt append remains first; feedback append is best-effort** — confirmed. `append_once` writes the G1A line, returns `False` on G1A failure (skipping G1B entirely), and on G1A success wraps the G1B import + call in a separate `try/except` that only logs at `debug`. Boot cannot crash from feedback append failure.
4. **CLI read path doesn't create/modify receipt files** — confirmed. `_read_jsonl` short-circuits on `not p.exists()` without touching the path; `cmd_receipts` only calls `iter_boot_receipts` + `summarize_boot_receipts` and `print`s. The test `test_memory_receipts_json_absent_file_is_stable` asserts the file is not created.
5. **Test coverage** — malformed JSONL ✓, missing files ✓, all five marker labels ✓, append-only ledger (uses `os.O_APPEND` + partial-line tolerance test) ✓, provider hook (asserts MEMORY.md/USER.md byte-identical) ✓, CLI read-only (asserts receipt bytes unchanged, no feedback ledger created) ✓, import-side-effect guard ✓.
6. **Goal 2 recall behavior not implemented** — confirmed. `recall_needed`, `recall_retrieved`, `recall_used`, `recall_skipped` appear only in `ALLOWED_EVENT_TYPES` as accepted strings; no code path emits them, and `G1B_STATUS.md` explicitly lists them as Goal 2 placeholders.
7. **Backwards-compatibility of `append_once` return type** — non-breaking. The sole production caller (`ChromaDBMemoryProvider._append_boot_synthesis_receipt` at `plugins/memory/chromadb/__init__.py:528`) ignores the return value. The new `bool` return is consumed only by `test_g1a_receipt_writer_appends_feedback_best_effort`. No `is None` / falsy checks exist on the result elsewhere in the tree.
