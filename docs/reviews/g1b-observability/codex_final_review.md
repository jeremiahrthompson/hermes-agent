Verdict: REQUEST_CHANGES

Critical Issues:
- None.

Important Issues:
- Real G1A receipt candidates use `source_metadata` / `target_metadata`, but G1B reads `candidate["metadata"]`. This means real `boot_selected` / `boot_dropped` events lose `source` and `target`, and receipt summaries report sources as `unknown`. See [g1b_observability.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1b_observability.py:138), [g1b_observability.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1b_observability.py:287), and the actual G1A shape in [g1a.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1a.py:351). The new tests use a non-real `metadata` fixture at [test_chromadb_g1b_observability.py](/Users/jeremiah/.hermes/hermes-agent/tests/plugins/memory/test_chromadb_g1b_observability.py:143), so they miss this.
- JSONL malformed accounting is lost when a file has no valid records. `_read_jsonl()` counts malformed lines, but `iter_boot_receipts()` only attaches that count to yielded valid records; then `summarize_boot_receipts([])` reports `missing=True` and `malformed_count=0`. See [g1b_observability.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1b_observability.py:76) and [g1b_observability.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1b_observability.py:100). This affects all-malformed and empty existing files.

Minor Issues:
- `hermes memory receipts --limit 0` is treated as default `100` because of `or 100` in [memory_setup.py](/Users/jeremiah/.hermes/hermes-agent/hermes_cli/memory_setup.py:463). Positive limits work, but the edge behavior is surprising and untested.
- `BootSynthesisReceiptWriter.append_once()` changing from `None` to `bool` is low-risk internally because callers ignore the return, but it is technically a behavior change for any external caller checking `is None`. See [g1a.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1a.py:518).

Required Fixes:
- Normalize candidate metadata in G1B to support both actual G1A receipt shape (`source_metadata` / `target_metadata`) and any legacy/raw `metadata` shape. Update summary and boot feedback conversion tests to use `g1a.candidate_receipt()` or equivalent real receipt fixtures.
- Preserve malformed/missing metadata even when zero valid JSONL records are yielded, and add tests for all-malformed and empty existing files.
- Add a focused test for `--limit` truncation and define `--limit 0` behavior explicitly.