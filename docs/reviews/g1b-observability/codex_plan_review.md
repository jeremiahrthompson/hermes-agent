**Verdict: APPROVED**

The proposed G1B shape fits the current repo and should stay provider-local, append-only, and testable without live Chroma/Forge/Sentinel access.

**Recommended Files / Functions**

- [plugins/memory/chromadb/g1a.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1a.py:504)
  - Keep G1A receipt schema stable.
  - Add a best-effort call from `BootSynthesisReceiptWriter.append_once()` after the boot receipt append succeeds to emit `boot_selected` / `boot_dropped` events into the G1B feedback ledger.

- New: `plugins/memory/chromadb/g1b_observability.py`
  - `iter_boot_receipts(...)`
  - `summarize_boot_receipts(...)`
  - `extract_correction_markers(text)`
  - `append_feedback_event(...)`
  - `iter_feedback_events(...)`
  - `summarize_feedback_events(...)`
  - `feedback_events_from_boot_receipt(...)`

- [plugins/memory/chromadb/__init__.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/__init__.py:137)
  - Add `ChromaDBMemoryProvider.on_turn_start(...)` to extract correction markers from user text and append `correction_marker` events.
  - Consider adding `on_session_switch(...)` if the ledger depends on `self._session_id`; Chroma currently does not override it.

- [hermes_cli/main.py](/Users/jeremiah/.hermes/hermes-agent/hermes_cli/main.py:11133)
  - Add a `hermes memory receipts` subparser.

- [hermes_cli/memory_setup.py](/Users/jeremiah/.hermes/hermes-agent/hermes_cli/memory_setup.py:456)
  - Route `memory receipts` to a small read-only display function.
  - Prefer delegating formatting to a new `hermes_cli/memory_observability.py` if output logic grows.

- New: `docs/memory/G1B_STATUS.md`
  - Document receipt summary schema, feedback ledger schema, event-type enum, non-goals, rollback/safety.

- New tests:
  - `tests/plugins/memory/test_chromadb_g1b_observability.py`
  - Optional CLI test: `tests/hermes_cli/test_memory_receipts.py`

**Scope Hazards**

- Do not implement Goal 2 recall behavior in G1B. Define `recall_needed`, `recall_retrieved`, `recall_used`, `recall_skipped` as allowed event types/placeholders only.
- Do not write raw user text to `memory_feedback.jsonl`; store marker labels plus `context_sha256`.
- Avoid scanning/rewriting `boot_synthesis.jsonl` during boot. Convert only the receipt already being appended.
- Do not call `initialize()` or any path that uses `get_or_create_collection()` in tests.
- “Receipts” conflicts with messaging read receipts in gateway code; CLI/help text should say “boot synthesis receipts.”

**Minimal Test Plan**

- Missing `boot_synthesis.jsonl` returns empty summary without creating files.
- Malformed JSONL lines are counted and skipped without raising.
- Summary covers candidate/selected/dropped IDs, fallback rates/reasons, latency stats, model counts, output hash counts, source counts, durability counts.
- Correction marker extraction returns structured labels for:
  - `you know this`
  - `we already discussed this`
  - `why are you asking`
  - `don't remember that`
  - `same as before`
- Feedback ledger append/read preserves required fields and is append-only.
- Boot receipt conversion emits `boot_selected` and `boot_dropped` events with source/target/durability/drop labels.
- Provider `on_turn_start()` appends only correction-marker events and does not touch Chroma, `MEMORY.md`, or `USER.md`.
- CLI `memory receipts` reads fake tmp `HERMES_HOME/logs` and prints/returns summary without mutation.

**Disagreements / Adjustments**

I would not make this a broad CLI project. A thin `hermes memory receipts [--json] [--limit N]` is feasible; anything interactive or repair-oriented should wait. I would also keep G1B in a new module rather than growing `g1a.py`, with only a tiny hook from the existing receipt writer.