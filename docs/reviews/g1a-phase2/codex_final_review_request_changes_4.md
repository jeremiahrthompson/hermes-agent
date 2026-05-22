Verdict: REQUEST_CHANGES
Summary:
Most third-pass repairs are in place, but one enabled-synthesis fallback still violates the receipt model contract.

Findings:
- [high] No-candidate enabled fallback now uses allowed `fallback_reason="exception"`, but it still records `model="legacy"` at `plugins/memory/chromadb/__init__.py:645-647`; the test locks that in at `tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py:239-247`. The contract reserves `legacy` for `memory.boot_synthesis_enabled=false`; enabled fallback receipts should keep `model="qwen2.5:7b"`.
- [medium] `diff_summary` can be computed against `output_text_preview` rather than reconstructable full prior block content (`g1a.py:470-490`, `__init__.py:602-613`). For prior blocks over 1000 chars, this can produce a misleading diff where the contract says `diff_summary` should be null unless previous content can be reconstructed or cached.

Required changes:
- Change the no-candidate enabled fallback receipt to record `model=qwen2.5:7b`, and update the unit test accordingly.
- Either compute `diff_summary` from full cached/reconstructable prior block content, or return `null` when only a truncated preview is available.

Evidence reviewed:
- `docs/memory/G1A_CONTRACT.md`
- `docs/reviews/g1a-phase2/staged.diff`
- `docs/reviews/g1a-phase2/live_manifest_smoke.json`
- `docs/reviews/g1a-phase2/live_prompt_probe.json`
- `docs/reviews/g1a-phase2/codex_final_review.md`
- `docs/reviews/g1a-phase2/codex_final_review_request_changes_2.md`
- `docs/reviews/g1a-phase2/codex_final_review_request_changes_3.md`
- `plugins/memory/chromadb/__init__.py`
- `plugins/memory/chromadb/g1a.py`
- `plugins/memory/chromadb/prompt_profile.py`
- `run_agent.py`
- `tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py`
- `tests/plugins/memory/g1a_live_manifest_smoke.py`
- `/Users/jeremiah/.hermes/config.yaml`