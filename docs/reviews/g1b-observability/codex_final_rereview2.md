Verdict: APPROVED

Critical Issues:
- None.

Important Issues:
- None.

Minor Issues:
- `plugins/memory/chromadb/g1b_observability.py:290`: `_candidate_metadata()` depends on merge precedence across `metadata`, `source_metadata`, and `target_metadata`. Current G1A data is handled, but explicit `source` / `target` selection would be clearer if those maps ever diverge.
- `tests/plugins/memory/test_chromadb_g1b_observability.py:59`: the “real G1A shape” fixture manually injects `source_metadata["collection"]`; live `candidate_receipt()` does not currently emit that field, so real boot feedback may have blank `collection`.
- `plugins/memory/chromadb/g1a.py:518`: `append_once()` now returns `bool`. Internal callers ignore it, so this is low risk, but a short docstring would make the API change explicit.

Required Fixes:
- None.

The prior blocker around `list(iter_boot_receipts(...))` metadata loss is now documented correctly in `docs/memory/G1B_STATUS.md:24`: callers must keep the `JsonlRecords` container for zero-valid-record metadata. The staged implementation meets the G1B scope and does not add Goal 2 recall behavior.