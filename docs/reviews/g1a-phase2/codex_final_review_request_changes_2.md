Verdict: REQUEST_CHANGES
Summary:
The major prior blockers are mostly repaired, but I cannot approve yet. Remaining issues are in receipt contract fidelity and one read-only verification artifact.

Findings:
- [high] Receipt `selected_ids` is not the IDs that survived into synthesized output. `_build_generated_profile_block()` records all deduped IDs, but `build_synthesis_prompt()` only includes up to 8 facts. Live receipt shows `selected_ids_count=31` while output has 8 bullets. See `plugins/memory/chromadb/__init__.py:637-645` and `plugins/memory/chromadb/g1a.py:420-449`.
- [high] The no-candidate fallback writes `fallback_reason="no_candidates"`, which is not in the contract enum. This can produce an invalid A5 receipt, and there is no test covering that path. See `plugins/memory/chromadb/__init__.py:641-643`.
- [medium] Candidate receipts still do not exactly match the nested candidate contract: they omit contract-named `composite_score` and top-level `stored_at`; `raw_score` is used ambiguously for composite score. See `plugins/memory/chromadb/g1a.py:351-371`.
- [medium] `live_prompt_probe.py` still calls `provider.initialize()`, which uses `get_or_create_collection()`. That is not strictly read-only against Chroma, even though the manifest smoke was repaired. See `docs/reviews/g1a-phase2/live_prompt_probe.py:10-12` and `plugins/memory/chromadb/__init__.py:313-325`.
- [medium] Live smoke `ok` does not require several worked-proof assertions it reports, including durable USER selection, duplicate drops, `gateway_session_key`, model, fallback state, or latency. The artifact values are good, but the verifier can pass without proving the contract. See `tests/plugins/memory/g1a_live_manifest_smoke.py:77-101`.

Required changes:
- Record only the IDs actually included in the synthesis prompt/output, or return prompt-selected IDs from `build_synthesis_prompt()`.
- Replace `no_candidates` with a contract-allowed fallback reason, or update the contract before implementation approval.
- Align candidate receipt fields with the contract and add schema tests for nested candidate objects.
- Make `live_prompt_probe.py` open Chroma read-only like the smoke test.
- Tighten live smoke assertions so `ok=true` proves every required worked-example condition.

Evidence reviewed:
- `docs/memory/G1A_CONTRACT.md`
- `docs/reviews/g1a-phase2/staged.diff`
- `docs/reviews/g1a-phase2/codex_final_review.md`
- `docs/reviews/g1a-phase2/live_manifest_smoke.json`
- `docs/reviews/g1a-phase2/live_prompt_probe.json`
- `plugins/memory/chromadb/g1a.py`
- `plugins/memory/chromadb/__init__.py`
- `plugins/memory/chromadb/prompt_profile.py`
- `tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py`
- `tests/plugins/memory/g1a_live_manifest_smoke.py`
- `/Users/jeremiah/.hermes/config.yaml`