# G1B Status — Memory observability and fact feedback

## Status

Implemented on branch `feat/memory-g1b-observability` after G1A merged to `main`.

G1B is an additive local-observability layer. It does not change Chroma retrieval, boot synthesis ranking, MEMORY.md / USER.md semantics, or Goal 2 first-turn recall behavior.

## What G1B adds

### Boot synthesis receipt reader

New module:

- `plugins/memory/chromadb/g1b_observability.py`

Read-only helpers:

- `read_boot_receipts(path)` — metadata-preserving read API for summaries and CLI use
- `iter_boot_receipts(path)` — compatibility alias returning the same record container
- `summarize_boot_receipts(records)`
- `boot_receipt_path_for_home(hermes_home)`

The reader tolerates missing receipt files, empty existing files, all-malformed files, and mixed valid/malformed JSONL lines. Missing files are not created by read paths. File-level parse metadata is preserved on the `JsonlRecords` container returned by `read_boot_receipts()` / `iter_boot_receipts()`; callers that need accurate `missing` and `malformed_count` for zero-valid-record files must pass that container to `summarize_boot_receipts()` rather than materializing a plain list first.

Summary fields include:

- `receipt_count`
- `malformed_count`
- `fallback_count`
- `fallback_rate`
- `fallback_reasons`
- `models`
- `latency_ms`
- `selected_ids`
- `dropped_ids`
- `drop_reasons`
- `sources`
- `durability`
- `output_hashes`

### Feedback ledger

Append-only local ledger:

- `$HERMES_HOME/logs/memory_feedback.jsonl`

Schema version:

- `schema_version: 1`

Required envelope fields:

- `timestamp`
- `session_id`
- `platform`
- `gateway_session_key`
- `event_type`
- `fact_id`
- `collection`
- `source`
- `target`
- `labels`
- `context_sha256`

Allowed event types:

- `boot_selected`
- `boot_dropped`
- `correction_marker`
- `recall_needed`
- `recall_retrieved`
- `recall_used`
- `recall_skipped`

The `recall_*` event types are placeholders for Goal 2 instrumentation. G1B does not perform retrieval or recall injection.

### Boot receipt to feedback conversion

`BootSynthesisReceiptWriter.append_once()` still writes the G1A receipt first. After the receipt append succeeds, it makes a best-effort append of corresponding G1B events:

- one `boot_selected` event per selected fact id
- one `boot_dropped` event per dropped fact id

Feedback append failures are debug-only and never raise into the boot path.

### Correction marker extraction

`extract_correction_markers(text)` detects structured labels:

- `you_know_this`
- `already_discussed`
- `why_asking`
- `dont_remember`
- `same_as_before`

Safety rule: G1B does not store raw user utterances in the feedback ledger. Correction-marker events store labels and SHA-256 hashes only, plus marker span offsets for local debugging.

### Provider hook

`ChromaDBMemoryProvider.on_turn_start()` appends `correction_marker` events when user text contains supported markers.

This hook is intentionally independent of Chroma availability and does not write to:

- ChromaDB
- Sentinel
- Forge
- MEMORY.md
- USER.md

### CLI surface

New thin command:

```bash
hermes memory receipts
hermes memory receipts --json
hermes memory receipts --limit 50
```

This command reads local boot synthesis receipts only. It does not create, repair, delete, or rewrite memory artifacts.

## Non-goals

G1B does not:

- implement Goal 2 first-turn semantic recall
- alter boot salience weights
- change retrieval/ranking behavior
- write to ChromaDB
- mutate MEMORY.md or USER.md
- change Chroma schemas or remote services
- add dynamic weighting
- perform stale-fact deletion

## Safety and rollback

G1B is additive. Runtime writes are limited to append-only local JSONL under `$HERMES_HOME/logs/memory_feedback.jsonl`.

Rollback options:

1. Disable G1A boot synthesis if boot feedback events should stop with boot receipts:

```bash
hermes config set memory.boot_synthesis_enabled false
```

2. Remove/ignore the local feedback ledger:

```bash
rm ~/.hermes/logs/memory_feedback.jsonl
```

No ChromaDB rollback is required because G1B does not write to ChromaDB.

## Verification

Focused tests:

```bash
uv run --with pytest python -m pytest -o addopts='' -q \
  tests/plugins/memory/test_chromadb_g1b_observability.py \
  tests/hermes_cli/test_memory_receipts.py
```

Focused result after Codex final-review blocker repair:

- `13 passed`

Expanded memory regression command:

```bash
uv run --with pytest python -m pytest -o addopts='' -q \
  tests/plugins/memory/test_chromadb_g1b_observability.py \
  tests/hermes_cli/test_memory_receipts.py \
  tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py \
  tests/plugins/memory/test_chromadb_generated_profile.py \
  tests/plugins/memory/test_chromadb_provider.py \
  tests/run_agent/test_memory_prompt_source.py
```

Expanded result:

- `85 passed`

Compile check:

```bash
uv run python -m py_compile \
  plugins/memory/chromadb/g1b_observability.py \
  plugins/memory/chromadb/g1a.py \
  plugins/memory/chromadb/__init__.py \
  hermes_cli/memory_setup.py \
  hermes_cli/main.py
```

Result:

- passed

## Deferred

Deferred to Goal 2:

- intent classification
- enforced first-turn semantic recall
- recall receipt emission during live retrieval
- tool/prompt injection of retrieved facts before clarifying questions

Deferred to later hygiene work:

- stale-fact cleanup
- duplicate-cluster review queues
- Chroma deletion/supersession workflows
- dashboard visualization
