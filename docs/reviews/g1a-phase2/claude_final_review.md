Verdict: APPROVED
Summary:
The fourth-pass implementation satisfies the contract's A1–A11 surface end-to-end. The five-signal scorer is centralized in `plugins/memory/chromadb/g1a.score_result` and reused by both the provider (`__init__.py:_score_results`) and the renderer (`prompt_profile._score`). The kill switch is properly routed through `_score_pre_g1a` + `legacy_pre_g1a=True`, restoring the original 0.5/0.3/0.2 ordering for the bit-identical path. Receipt writes are guarded exact-once per session, populate all 19 required fields with millisecond UTC timestamps, strip embedding-shaped arrays, and distinguish timeout/empty_output/model_unreachable/unsafe_output/chroma_unreachable/kill_switch_off/exception. Fallback `model` stays `qwen2.5:7b` whenever synthesis was attempted, and the no-candidate path correctly uses the `exception` enum value with a covering unit test. Live manifest smoke proves a real 629-char synthesized block under 8s with 7 duplicate drops, durable USER selection, gateway_session_key field presence, and MEMORY.md/USER.md SHA unchanged.

Findings:
- [low] Time-bound classifier keyword list (`job`, `deal`, `active`, `current project`, …) can plausibly mis-classify durable user preferences as time-bound, and `filter_candidates` drops time-bound rows without `valid_until` entirely. Today's live probe shows 20 USER rows surviving, so observed risk is low, but a future durable preference phrased around `job`/`active` would be silently filtered. Worth a follow-up tightening, not a blocker.
- [low] `SOURCE_QUALITY` maps the literal strings `memory` and `user` to 1.0. These are normally `target` values, so this is effectively a quiet catch-all for legacy rows whose `source` got populated from `target`. Functionally fine; comment it or drop it next iteration.
- [low] Receipt now stores full `output_text` (up to 2200 chars) plus `output_text_preview` (≤1000 chars). Diff fidelity is better than diffing against a truncated preview, but JSONL grows ~3 KB per boot; if retention isn't bounded elsewhere, plan rotation later.
- [low] Kill-switch identity is proven via `_score_pre_g1a` matching the prior `_score` body, but the test compares two within-tree calls rather than a captured pre-change baseline SHA. Code inspection confirms the formula and inputs are unchanged; a recorded pre-change golden could harden this in CI later.
- [info] Live latency 6206ms / 6434ms leaves only ~1.6s headroom before the 8s ceiling. Acceptable today; consider tracking p95 once boots accumulate to detect cold-load regressions early.

Required changes:
- (none blocking; address the low-severity items in a follow-up)

Evidence reviewed:
- Embedded staged diff (full)
- `plugins/memory/chromadb/g1a.py` — scoring constants, source/durability classifiers, dedup, prompt builder, Ollama client, receipt writer, vector stripping
- `plugins/memory/chromadb/__init__.py` — `_build_generated_profile_block`, `_build_legacy_generated_profile_block`, `_append_boot_unavailable_receipt`, kill-switch routing, init kwargs bridge
- `plugins/memory/chromadb/prompt_profile.py` — `_score` (G1A) and `_score_pre_g1a` (legacy) with `legacy_pre_g1a` parameter
- `hermes_cli/config.py` DEFAULT_CONFIG and `run_agent.py` init bridge
- `tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py` (A1–A11 unit tests)
- `tests/plugins/memory/g1a_live_manifest_smoke.py` (read-only Mac smoke)
- `tests/plugins/memory/test_chromadb_generated_profile.py` (legacy-pinned via `_boot_synthesis_enabled=False`)
- `docs/reviews/g1a-phase2/live_manifest_smoke.json` (ok true, qwen2.5:7b, 6206ms, 8 selected, MEMORY/USER unchanged)
- `docs/reviews/g1a-phase2/live_prompt_probe.json` (single valid JSON, 8 prompt-selected ids, 731-char synthesized output)
- Codex final-review trail (`codex_final_review.md`, `_request_changes_2/3/4.md`) — confirmed all four blockers landed in the diff
