You are an independent read-only planning lane for Hermes Agent memory upgrade G1B.

Repo: /Users/jeremiah/.hermes/hermes-agent
Branch target: feat/memory-g1b-observability
Scope: Mac/Rilo Hermes Agent only. Do not mutate Forge/Sentinel/Chroma services. Local repo code/docs/tests only.

Context:
- G1A has merged: boot-time Chroma synthesis with receipts at ~/.hermes/logs/boot_synthesis.jsonl, code in plugins/memory/chromadb/g1a.py and plugins/memory/chromadb/__init__.py, tests in tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py.
- Next upgrade is G1B: memory observability and fact feedback counters.
- Goal is to make salience tuning evidence-based and prepare for Goal 2 enforced first-turn semantic recall.

Desired G1B implementation shape:
1. Add local read-only/append-only observability helpers for boot_synthesis receipts:
   - parse ~/.hermes/logs/boot_synthesis.jsonl safely
   - summarize selected/dropped/candidate IDs, fallback rates, latency, model, output hashes, source/durability distributions
   - tolerate malformed lines and missing files without crashing
2. Add correction marker extraction for user text:
   - markers such as "you know this", "we already discussed this", "why are you asking", "don't remember that", "same as before"
   - produce structured labels, not free text only
3. Add fact feedback ledger, append-only local JSONL under ~/.hermes/logs/memory_feedback.jsonl or equivalent:
   - timestamp/session_id/platform/event_type/fact_id/collection/source/target/labels/context_sha256
   - event types should cover boot_selected, boot_dropped, correction_marker, recall_needed/retrieved/used/skipped placeholders for Goal 2
   - must not write to ChromaDB or mutate MEMORY.md/USER.md
4. Add CLI surface if feasible under hermes_cli, e.g. `hermes memory receipts` or a small module callable by tests. If CLI scope is too broad, recommend module-only v1 and explain.
5. Add docs/memory/G1B_STATUS.md or CONTRACT-ish doc describing schema, non-goals, rollback/safety.
6. TDD: tests for receipt parsing, malformed JSONL, summary rollups, correction marker extraction, feedback append/read, no Chroma/flat-file mutation.

Please inspect the repo read-only and return:
- Verdict: APPROVED or REQUEST_CHANGES for this implementation plan
- Exact files/functions you recommend touching
- Any scope hazards
- Minimal test plan
- Any disagreements with the proposed shape
