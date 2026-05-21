# G1A Phase 2 status — boot-time Chroma memory synthesis

Updated: 2026-05-21

## Built in Phase 2

- Added G1A boot synthesis helpers for ChromaDB memory:
  - static v1 salience scoring: 0.35 semantic, 0.25 recency, 0.20 source_quality, 0.15 importance, 0.05 durability
  - source_quality classifier honoring builtin_mirror / hand-authored equivalents, seed, pre_compress_extraction, session_turn, and missing/unknown sources
  - deterministic durability classifier with durable / time-bound / ephemeral labels
  - ephemeral and stale/out-of-window time-bound filtering
  - normalized content SHA-256 dedup and embedding cosine near-duplicate dedup at threshold 0.92
  - representative selection ordered by source_quality, importance, durability, recency, similarity
  - qwen2.5:7b Ollama synthesis client with 8s timeout and fallback exceptions
  - structured boot receipt writer for ~/.hermes/logs/boot_synthesis.jsonl
- Wired the Chroma provider generated-profile path through the G1A synthesis layer when memory.boot_synthesis_enabled is true.
- Preserved the pre-G1A deterministic generated-profile path as _build_legacy_generated_profile_block().
- Added the kill switch memory.boot_synthesis_enabled with default true in hermes_cli.config.DEFAULT_CONFIG and bridged it from run_agent.py into provider.initialize().
- Set /Users/jeremiah/.hermes/config.yaml memory.boot_synthesis_enabled: true for the MacBook Rilo instance.
- Added unit tests covering scoring, source quality, durability filtering, exact and embedding dedup, receipt exact-once/schema, happy path synthesis, model fallback, kill-switch bit-identity, MEMORY.md / USER.md immutability, config bridge, and degraded-additive prompt-source policy.

## Deferred explicitly by contract

- Goal 1B: per-fact access counters and correction-feedback signals for future salience tuning.
- Goal 2: enforced first-turn semantic recall for high-risk intents; G1A only improves boot context and observability.
- Goal 3: intent classification and task-routing for retrieval.
- Dynamic salience weighting by request complexity.
- Changes to the upstream 2,200 / 1,375 character caps.
- Any change to MEMORY.md / USER.md write semantics, memory_tool behavior, or builtin_mirror mirroring.

## Controller-review decisions / caveats

- Existing legacy generated-profile unit tests are pinned to the kill-switch path so they continue to verify pre-G1A bit-identical behavior without making local Ollama calls. The new G1A tests cover the enabled synthesis path with fakes.
- Empty post-filter candidate sets now fall back to the legacy deterministic block instead of asking Ollama to synthesize from no facts; this prevents hallucinated boot-memory content.
- Final-review repair tightened receipt semantics: Chroma-unreachable boots now append one receipt, synthesis timeout/empty/unreachable/unsafe fallback reasons are distinguished, and attempted-synthesis fallbacks keep model=qwen2.5:7b in receipts.
- The kill switch now routes deterministic profile ranking through the exact pre-G1A 0.5/0.3/0.2 score path while the enabled renderer path uses the G1A five-signal formula.
- The live manifest smoke now opens Chroma collections read-only with get_collection rather than get_or_create_collection.
- The implementation PR must remain unmerged until explicit controller authorization after dual-lane final review and CI observation.
