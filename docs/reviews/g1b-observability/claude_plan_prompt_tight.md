You are the Claude Opus 4.7 independent planning lane for Hermes Agent G1B.

No tools. Do not inspect files. Review this supplied plan and return a concise verdict only.

Scope: Mac/Rilo Hermes Agent repo only. No Sentinel/Forge/Chroma service mutations. Local repo code/docs/tests only.

G1A already merged: boot synthesis receipts at ~/.hermes/logs/boot_synthesis.jsonl; code in plugins/memory/chromadb/g1a.py and plugins/memory/chromadb/__init__.py; tests in tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py.

G1B proposed implementation:
1. New provider-local pure module plugins/memory/chromadb/g1b_observability.py:
   - iter_boot_receipts(path)
   - summarize_boot_receipts(records)
   - extract_correction_markers(text)
   - append_feedback_event(path, event)
   - iter_feedback_events(path)
   - summarize_feedback_events(records)
   - feedback_events_from_boot_receipt(receipt)
2. Boot synthesis writer in plugins/memory/chromadb/g1a.py remains schema-stable but after appending a boot receipt, best-effort appends boot_selected and boot_dropped feedback events to ~/.hermes/logs/memory_feedback.jsonl.
3. ChromaDBMemoryProvider.on_turn_start in plugins/memory/chromadb/__init__.py extracts correction markers from user text and appends correction_marker feedback events only. It must not write to ChromaDB, MEMORY.md, or USER.md.
4. Thin CLI surface only: hermes memory receipts [--json] [--limit N] in hermes_cli/main.py and/or hermes_cli/memory_setup.py, reading local logs only.
5. Docs: docs/memory/G1B_STATUS.md with schema, non-goals, safety, rollback.
6. TDD tests:
   - missing boot_synthesis.jsonl returns empty summary without creating files
   - malformed JSONL counted/skipped without raising
   - summary covers selected/dropped/candidate IDs, fallback rates, latency, model/hash/source/durability distributions
   - correction markers detect labels: you_know_this, already_discussed, why_asking, dont_remember, same_as_before
   - feedback ledger append/read preserves required fields and is append-only
   - boot receipt conversion emits boot_selected/boot_dropped with source/target/durability/drop labels
   - provider on_turn_start appends only correction_marker and does not touch Chroma/flat files
   - CLI reads fake tmp HERMES_HOME/logs and prints/returns summary without mutation

Known Codex planning lane verdict: APPROVED, with hazards: do not implement Goal 2 retrieval behavior; do not write raw user text to feedback ledger; avoid boot-time scans; tests must not initialize live Chroma; CLI should say boot synthesis receipts to avoid messaging receipt ambiguity.

Return exactly:
Verdict: APPROVED or REQUEST_CHANGES
Critical Issues: ...
Recommended Adjustments: ...
Minimal Test Plan: ...
