You are an independent final review lane for Hermes Agent Goal 1A Phase 2 implementation after the fourth REQUEST_CHANGES repair pass.

Repository: /Users/jeremiah/.hermes/hermes-agent
Branch: feat/memory-g1a-implementation
Contract: docs/memory/G1A_CONTRACT.md from merged PR #9 commit 4aa4a1a.
Scope guard: Mac-only. Allowed code/config scope: this repo and /Users/jeremiah/.hermes/config.yaml. ChromaDB 100.107.68.104:8000 and Forge embedding 100.113.1.2:8006 are read-only. No Chroma writes/schema changes, no Forge/Sentinel restarts, no deploys, no merge.

Review the staged diff in docs/reviews/g1a-phase2/staged.diff plus verification artifacts in docs/reviews/g1a-phase2/.
Controller-run verification after fourth repair:
- uv run python -m py_compile plugins/memory/chromadb/g1a.py plugins/memory/chromadb/__init__.py plugins/memory/chromadb/prompt_profile.py hermes_cli/config.py run_agent.py tests/plugins/memory/g1a_live_manifest_smoke.py docs/reviews/g1a-phase2/live_prompt_probe.py => PASS
- uv run --with pytest python -m pytest -o addopts='' -q tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py tests/plugins/memory/test_chromadb_generated_profile.py tests/plugins/memory/test_chromadb_provider.py tests/run_agent/test_memory_prompt_source.py => 72 passed
- Live read-only manifest smoke against Chroma/Forge/Ollama: docs/reviews/g1a-phase2/live_manifest_smoke.json => ok true, Chroma count 2316, block 629 chars, model qwen2.5:7b, latency 6206ms, dropped duplicates 7, selected_ids_count 8, selected durable USER true, gateway_session_key field present, MEMORY/USER SHA unchanged.
- Live read-only prompt probe: docs/reviews/g1a-phase2/live_prompt_probe.json => valid single JSON doc, qwen2.5:7b success, prompt_selected 8, prompt 2915 chars, latency 6434ms, output 731 chars.
- Previous Codex REQUEST_CHANGES reports are saved at docs/reviews/g1a-phase2/codex_final_review_request_changes_2.md, docs/reviews/g1a-phase2/codex_final_review_request_changes_3.md, and docs/reviews/g1a-phase2/codex_final_review.md; specifically re-check those blockers.

Repair notes:
- No-candidate fallback now uses contract-enum fallback_reason=exception, records model=qwen2.5:7b, and has a unit test.
- Chroma-unreachable enabled-synthesis receipts record model=qwen2.5:7b.
- diff_summary now uses prior full output_text when available; otherwise it is null rather than diffing against a truncated preview. Receipts include output_text plus output_text_preview.
- live_prompt_probe.json is a single valid JSON document.
- Receipt selected_ids records only prompt-selected facts actually sent to qwen (<=8); non-selected surviving facts are logged as dropped reason=over_budget.
- Candidate receipt objects include composite_score and top-level stored_at in addition to raw_score/source_metadata/target_metadata.
- live_prompt_probe.py and manifest smoke both open Chroma read-only with get_collection, not get_or_create_collection.
- Manifest smoke ok=true requires model=qwen2.5:7b, no fallback, latency<=8000, duplicate drop evidence, selected durable USER, gateway_session_key field presence, selected_ids_count<=8, required fields, <=2200 chars, and MEMORY/USER unchanged.

Required review dimensions:
1. Contract parity with G1A_CONTRACT.md expected assertions A1-A11.
2. Correctness of salience formula and prompt_profile parity while preserving kill-switch identity.
3. source_quality/durability/dedup behavior and false-positive risks.
4. qwen2.5:7b 8s timeout/fallback semantics.
5. Receipt schema/exact-once/usefulness and no raw embeddings.
6. Kill switch bit-identical legacy path.
7. Mac-only scope and remote read-only scope; no MEMORY.md/USER.md writes.
8. Test coverage sufficiency and remaining CI risk.

Return exactly this format:
Verdict: APPROVED or REQUEST_CHANGES or BLOCK
Summary:
Findings:
- [severity] ...
Required changes:
- ...
Evidence reviewed:
