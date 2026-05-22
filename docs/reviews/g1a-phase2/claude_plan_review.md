Verdict: APPROVE_PLAN

Key plan:
- Branch hygiene: confirm `feat/memory-g1a-implementation` is rebased on merged PR #9 (4aa4a1a). All edits stay inside this repo plus `/Users/jeremiah/.hermes/config.yaml`. No edits to `MEMORY.md`, `USER.md`, `chromadb.json`, Sentinel, or Forge.
- Preflight (read-only): verify Chroma at 100.107.68.104:8000 reachable, `agent_memories` count within drift bounds (≥2000, source-class drift ≤10%), Forge at 100.113.1.2:8006 reachable, local Ollama responds with `qwen2.5:7b` under ~7s on a warm prompt. Record the numbers; abort and re-open contract if pivot threshold tripped.
- TDD pass 1 — pure helpers in new `plugins/memory/chromadb/g1a/` (or sibling modules under `plugins/memory/chromadb/`): write failing unit tests first, then minimum implementation, for:
  1. `score_components()` returning sim/rec/src/imp/dur components and `composite_v1 = 0.35·sim + 0.25·rec + 0.20·src + 0.15·imp + 0.05·dur` (must sum to 1.00; assert at import time).
  2. `source_quality(meta)` ordering builtin_mirror==hand-authored > seed > pre_compress_extraction > session_turn == missing.
  3. `durability_label(content, meta)` returning `durable|time-bound|ephemeral` with deterministic heuristics; never writes to Chroma.
  4. `normalize(text)` (NFKC+lower+trim+collapse+strip volatile punctuation) and `content_hash()` SHA-256 collapse.
  5. `near_duplicate(emb_a, emb_b, threshold=0.92)` cosine; representative-selection tie-break order from contract §Deduplication.
  6. Validity-window evaluator: stale time-bound and ephemeral exclusion.
  7. Receipt schema validator (required fields, enum reasons, no embeddings in payload).
- TDD pass 2 — scoring parity: update `plugins/memory/chromadb/__init__.py:1352 _score_results` and `plugins/memory/chromadb/prompt_profile.py:117 _score` to use the same shared `score_components()` so provider scoring and renderer cannot drift. Replace `_W_SIM/_W_REC/_W_IMP` constants with a single shared module; tests prove identical outputs across both call sites. Keep `_RECENCY_WINDOW = 30d` from `__init__.py:1355`.
- TDD pass 3 — config bridge:
  1. Add `memory.boot_synthesis_enabled: true` default in core config defaults (the same path that owns `prompt_source`). Test default is `True`.
  2. Test core `AIAgent` passes `boot_synthesis_enabled` (plus `platform`, `gateway_session_key`, `session_id`, `hermes_home`) into `MemoryProvider.initialize(**kwargs)`.
  3. Provider stores `self._boot_synthesis_enabled` from kwargs; never reads `hermes_cli.config` directly (preserves the existing transport pattern from `__init__.py:240-247`).
  4. Test that `hermes config set memory.boot_synthesis_enabled false` (or documented YAML fallback) → next session boot is bit-identical to pre-change.
- TDD pass 4 — synthesis client: new `plugins/memory/chromadb/g1a/ollama_synth.py` with a hard 8s timeout (use `requests` or `httpx` with deadline; never `subprocess`). Tests use a fake HTTP responder for happy path, timeout, empty, unsafe, and connection-refused; never hits real Ollama in unit tests.
- TDD pass 5 — receipt writer: new `plugins/memory/chromadb/g1a/receipt.py` appending exactly one JSONL line to `~/.hermes/logs/boot_synthesis.jsonl`. Per-session/per-provider idempotence guard (in-memory flag plus file-level append check on the session_id). Receipt write failures log via `logger.warning` and never raise to caller. Test: two calls in one session → one receipt line.
- TDD pass 6 — orchestrator wired into the existing `system_prompt_block()` path (the seam already exposed via `external_system_prompt_block()` at `agent/memory_manager.py:283`). Flow:
  1. If `boot_synthesis_enabled=False` → return legacy non-synthesized Chroma block (pre-change code path) and append receipt with `model="legacy"`, `fallback_path_taken=true`, `fallback_reason="kill_switch_off"`, `input_chars=0`, `output_chars=len(legacy_block)`.
  2. Else collect candidates via existing retrieval, score with v1 weights, exclude ephemeral, drop stale time-bound, dedup (hash then 0.92 cosine), enforce 2200-char cap.
  3. Call Ollama `qwen2.5:7b` with 8s deadline.
  4. On timeout/empty/unsafe/connection/exception → legacy non-synthesized Chroma block; receipt records the matching `fallback_reason`. Block must remain additive in `provider_with_legacy_fallback`; degraded blocks must not suppress legacy (`run_agent.py:6345` invariant preserved).
- TDD pass 7 — pytest A1–A11 (see checklist) using fakes for Ollama/Chroma plus fixtures that hash the assembled provider block.
- TDD pass 8 — manifest live smoke test (Mac-only marker, skipped in CI), read-only against `agent_memories`: assert 2200-char cap, receipt completeness, ≥1 durable `target=user` fact, SpaceX/Anduril-class dedup collapse, `MEMORY.md`/`USER.md` SHA-256 unchanged pre/post.
- Final wiring: bridge value in `AIAgent` constructor/init that already passes `prompt_source`/`generated_prompt_enabled` to `initialize()`. Update `chromadb.json` schema docs (no behavior change). Append `docs/memory/G1A_STATUS.md` last.
- Stop-before-merge: open PR, dual-lane review, no merge.

Contract parity checklist:
- A1: integration test with fake reachable Chroma + fake Ollama returning 1.5KB string in ≤200ms → non-empty block ≤2200, latency receipt < 8000ms.
- A2: fake Chroma client raises `ConnectionError` → legacy block path, receipt `fallback_reason ∈ {chroma_unreachable, exception}`, session boots.
- A3: fake Ollama refuses → legacy block, `fallback_reason="model_unreachable"`.
- A4: fixture loads provider twice — once with `boot_synthesis_enabled=true` but kill-switch-flipped mid-test to false, second boot SHA-256 of provider block equals pre-change baseline. Assert `model="legacy"`, `fallback_reason="kill_switch_off"`.
- A5: pytest reads JSONL after a boot, asserts `len(lines)==1`, every required field is present and well-typed, and no field contains float arrays (embedding guard).
- A6: capture SHA-256 of `MEMORY.md` and `USER.md` pre/post; equality assertion.
- A7: two candidates differing only in case/whitespace/punctuation → one survives; dropped entry has `reason="duplicate"`.
- A8: synthesized embeddings with cosine 0.94 → one survives; dropped entry has `reason="duplicate"` (or `"superseded"` when representative-selection tie-break replaces a prior pick).
- A9: candidates flagged ephemeral never enter `selected_ids`; receipt lists them in `dropped_ids` with `reason="ephemeral"`.
- A10: time-bound candidate with `valid_until` < now → excluded with `reason="out_of_validity_window"`; time-bound within window → eligible.
- A11: degraded provider block fixture → assert `_suppress_legacy_memory` remains false at `run_agent.py:6347` (re-use the existing prompt-source matrix; no new branches added).

Mac-only/scope notes:
- All edits limited to `/Users/jeremiah/.hermes/hermes-agent/**` plus, at most, `/Users/jeremiah/.hermes/config.yaml`. No `chromadb.json` writes for the kill switch — bridge through `initialize(**kwargs)`, matching the `prompt_source` transport at `plugins/memory/chromadb/__init__.py:240-247`.
- Live tests are read-only against ChromaDB and Forge: use `get_collection().get(...)` / `query(...)` paths only; no `add/upsert/delete/update`, no `_init_client` schema mutation, no embedding-service POSTs that train or alter state. Mark live tests with `pytest.mark.live_mac` and skip by default.
- The only filesystem writes outside the repo are `~/.hermes/logs/boot_synthesis.jsonl` (append-only, created if missing) and the user-invoked rollback edit to `/Users/jeremiah/.hermes/config.yaml`.
- Do not restart Sentinel/Forge, do not deploy, do not merge.

Risks/pivots:
- Receipt exact-once is the highest-risk requirement: an exception thrown between assembly and append can either skip the receipt (violates "must append even on fallback") or double-append (violates A5). Mitigate with a single try/finally around assembly that always passes through the writer plus a per-session guard. Add an explicit test for "exception during synthesis still produces exactly one receipt with `fallback_reason='exception'`."
- `qwen2.5:7b` 8s budget against 6.18s baseline is tight; cold-load can spike. Pivot if observed p95 > 7.5s on Mac during preflight — re-open contract before code lands rather than silently widening the timeout.
- Forge unavailability blocks the 0.92 cosine dedup live test. If Forge is down for the testing window, gate the live test on reachability and document the skip; pivot if Forge is durably unreachable.
- Source-class drift (e.g., new high-volume source class, >10% movement, count <2000) → re-open contract instead of patching ordering.
- Durability classifier is deterministic-first; if heuristics misclassify a SpaceX/Anduril-class fact as ephemeral, A9/A10 will silently strip durable content. Add fixture-based tests covering known durable identity facts and known ephemeral PR/commit lines before relying on the classifier in the smoke test.
- Bit-identical kill-switch path (A4) is fragile if any imported helper changes default whitespace/ordering. Snapshot the assembled provider block under the kill-switch fixture at the same call site as `external_system_prompt_block()` and assert SHA-256 equality across pre/post-implementation runs in CI.
- Cache the previous-block SHA for `previous_block_sha256`/`diff_summary` from the prior receipt only — never introduce a new persisted boot-memory cache file; it would create a second source of truth and violate the ownership-seam constraint.
