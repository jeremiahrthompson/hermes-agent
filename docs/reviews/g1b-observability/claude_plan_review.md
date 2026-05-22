Verdict: APPROVED

Critical Issues:
- None blocking. The plan is well-scoped to provider-local additive surfaces, preserves the G1A receipt schema, and explicitly excludes mutating ChromaDB, MEMORY.md, USER.md, and external services. The on_turn_start hook is correctly constrained to append-only feedback events. No retrieval/ranking behavior changes are proposed, which keeps Goal 2 out of scope as required.

Recommended Adjustments:
- Privacy on correction markers: store only the matched label token(s) plus a coarse offset/length or a stable hash of the trigger span — never the raw user utterance. Document this in G1B_STATUS.md under safety.
- Feedback event schema: pin a versioned envelope (e.g. schema_version, event_type, ts_utc, source, session_id_hash) before any writes land, so later goals can evolve without rewriting the ledger.
- Best-effort writer hygiene in g1a.py: wrap the new feedback append in a guarded try/except that swallows IO errors and never raises into the boot path; log to stderr at debug only. Confirm ordering: boot receipt is durably appended first, then feedback events — never the reverse.
- File handling: open ledger appends with O_APPEND semantics and a single write() per record (newline-terminated) to keep append atomicity on POSIX; document the cross-process concurrency assumption (single-writer expected, readers tolerant of partial trailing lines).
- CLI wording: rename surface to `hermes memory receipts` outputting "boot synthesis receipts" in human text (matches Codex hazard). Add `--since <iso8601>` later only if needed — do not add now.
- iter_* functions must be generators that skip malformed lines with a counter returned via the summarize_* path, not via logging side effects, to keep purity.
- Explicitly assert in tests that no network calls and no chromadb client construction occur during G1B code paths (monkeypatch or import-guard).
- Add a non-goal line to G1B_STATUS.md: "does not influence retrieval, ranking, or memory writes in this goal."

Minimal Test Plan:
1. iter_boot_receipts: missing file → empty iterator, no file creation (assert path does not exist after call).
2. iter_boot_receipts: mixed valid/invalid JSONL → yields only valid records; summarize reports skipped_count.
3. summarize_boot_receipts: covers selected/dropped/candidate IDs, fallback rate, latency aggregates (count/min/max/avg), and distributions over model, hash, source, durability.
4. extract_correction_markers: detects each of you_know_this, already_discussed, why_asking, dont_remember, same_as_before with representative phrasings; returns labels + span metadata, never raw text.
5. append_feedback_event + iter_feedback_events: round-trip preserves required fields, append-only (offset grows monotonically), tolerant of partial trailing line.
6. feedback_events_from_boot_receipt: emits one boot_selected per selected ID and one boot_dropped per dropped ID, each carrying source/target/durability and drop label where applicable.
7. g1a writer integration: after a boot receipt append, feedback ledger contains matching boot_selected/boot_dropped events; injected IO failure on ledger does not raise and does not corrupt the receipt file.
8. ChromaDBMemoryProvider.on_turn_start: with monkeypatched Chroma client and flat-file writers asserted untouched, a user message containing a marker appends exactly one correction_marker event; a message without markers appends nothing.
9. CLI `hermes memory receipts`: with tmp HERMES_HOME containing a synthetic boot_synthesis.jsonl, prints human summary; with --json emits stable schema; with --limit N truncates; exits 0 when file absent and prints empty-summary message; asserts no writes to HERMES_HOME after invocation.
10. Guard test: importing plugins/memory/chromadb/g1b_observability does not import chromadb and makes no network calls (sys.modules and socket monkeypatch assertions).
