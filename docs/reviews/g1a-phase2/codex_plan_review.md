Verdict: APPROVE_PLAN

Key plan:
- Add failing tests first for pure helpers: five-signal scoring, `source_quality`, durability labels, exact normalized hash dedup, cosine `>=0.92` dedup, stale time-bound exclusion, ephemeral exclusion, and receipt schema validation.
- Add config tests: `DEFAULT_CONFIG["memory"]["boot_synthesis_enabled"] == True`, `hermes config set memory.boot_synthesis_enabled false` works, and `AIAgent` bridges the loaded YAML value into Chroma provider `initialize(...)`.
- Implement provider-local helpers in `plugins/memory/chromadb/`: scoring constants/shared score component builder, source/durability classifiers, dedup selection, receipt writer, Ollama synthesis client with `qwen2.5:7b` and hard `8s` timeout.
- Update both `plugins/memory/chromadb/__init__.py::_score_results()` and `prompt_profile.py` ranking to the same `0.35/0.25/0.20/0.15/0.05` semantics.
- Thread provider init metadata: `boot_synthesis_enabled`, `platform`, `gateway_session_key`, session id, Hermes home. Keep `run_agent.py` prompt suppression logic unchanged except for this bridge.
- Wrap boot synthesis inside `system_prompt_block()` / generated-profile path with a per-provider/per-session receipt guard so one boot appends at most one JSONL object.
- Fallback order: kill switch false returns pre-change non-synthesized block bit-identically; Chroma/model/timeout/empty/unsafe/exception falls back to legacy non-synthesized Chroma block or empty provider block under existing core policy, never crashing.
- Add integration-style pytest using fakes for A1-A11, then a manifest smoke test gated for Mac/live services that reads Chroma/Forge only and verifies required live assertions.

Contract parity checklist:
- A1: fake/live reachable path proves non-empty synthesized block, `<=2200`, under `8s`.
- A2: Chroma failure test appends receipt with `chroma_unreachable` or `exception`.
- A3: Ollama failure test appends `model_unreachable`.
- A4: kill switch false hashes pre/post assembled provider block or system prompt for bit identity.
- A5: receipt JSONL exactly once, all required fields populated, no embeddings.
- A6: pre/post SHA-256 for `MEMORY.md` and `USER.md`.
- A7: exact normalized duplicate collapse.
- A8: cosine `>=0.92` near-duplicate collapse.
- A9: `ephemeral` excluded.
- A10: stale/out-of-window `time-bound` excluded.
- A11: degraded/fallback blocks do not suppress legacy memory beyond existing prompt-source policy.

Mac-only/scope notes:
Implementation should only touch this repo and `/Users/jeremiah/.hermes/config.yaml`. Live ChromaDB and Forge checks must be read-only: no upserts, schema changes, collection rewrites, service restarts, deploys, or merge. Prefer `get_collection`/query-only smoke paths for live tests, and keep writes limited to local config plus `~/.hermes/logs/boot_synthesis.jsonl`.

Risks/pivots:
- Pivot if live source distribution drifts beyond contract thresholds or new high-volume source classes appear.
- Pivot if `qwen2.5:7b` cannot reliably meet `8s`.
- Pivot if Forge is unavailable for embedding dedup validation.
- Treat receipt exact-once as high risk; add an idempotence guard before implementation proceeds.
- Avoid adding a new boot memory source of truth; receipts are observability only, and existing cache use must not replace Chroma/flat-file ownership.