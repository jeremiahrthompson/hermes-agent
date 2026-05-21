# G1A contract: boot-time Chroma memory block

PR title: feat(memory): G1A contract — boot-time Chroma memory block

## Goal

Promote the Chroma `external_system_prompt_block` into the first-class boot memory artifact for the MacBook Rilo instance, with observability receipts, deterministic rollback, and explicit non-claims for deferred recall/routing work.

This document is the Phase 1 docs-only contract. It does not implement boot synthesis, mutate Chroma, edit config, or change runtime behavior.

## Scope guard

In scope:

- MacBook Rilo only.
- Repository: `/Users/jeremiah/.hermes/hermes-agent/`.
- Runtime config path to be referenced by the implementation PR: `/Users/jeremiah/.hermes/config.yaml`.
- Read-only access only to:
  - ChromaDB on Sentinel: `100.107.68.104:8000`.
  - Forge embedding service: `100.113.1.2:8006`.

Out of scope and forbidden for this goal:

- Writes to ChromaDB, schema migrations, collection rewrites, data cleanup, or service restarts on Sentinel.
- Writes, model deployment, or service restarts on Forge.
- Deployment to any Hermes instance other than this MacBook Rilo checkout.
- Merge to `main` without explicit controller authorization.
- Any implementation change in Phase 1.

Abort condition: if a sub-agent proposes work outside this scope, the controller must stop that lane and surface the scope violation.

## Architecture / Design

G1A is a provider-local boot-memory synthesis layer that preserves the existing Hermes core prompt-source policy. Retrieval and ranking remain in the Chroma memory provider; deterministic rendering remains in the generated profile path; boot prompt assembly remains in `run_agent.py`. The implementation must be additive around the existing `external_system_prompt_block()` seam rather than replacing the memory store, changing `memory_tool`, or adding a second source of truth. The design boundary is: flat files own durable curated writes, Chroma owns vector search and synthesized boot context, and receipts own boot observability.

## Non-goals

G1A does not repair first-turn high-risk semantic recall, does not add intent routing, does not tune dynamic salience weights, does not mutate Chroma facts, does not rewrite `MEMORY.md` or `USER.md`, does not change the `builtin_mirror` bridge, and does not deploy anything beyond the MacBook Rilo checkout. These exclusions are repeated in the detailed deferred-scope section to make the plan-control-plane and reviewer gates unambiguous.

## Open Questions

None for Phase 1 after dual-lane planning reconciliation. The three planning clarifications are resolved in this contract: legacy `seed` and `session_turn` sources are honored on read; the kill switch is intentionally in `/Users/jeremiah/.hermes/config.yaml` and bridged into provider initialization; `2286 -> 2294` live Chroma count drift is acceptable live-data drift unless later pivot thresholds are crossed.

## Global Execution Rules

The controller owns git operations, branch creation, commits, PR creation, test execution, reviewer reconciliation, and final merge decisions. Sub-agents are read-only unless a later phase explicitly authorizes an implementation lane. No sub-agent may write to Chroma, restart Sentinel or Forge services, modify another Hermes instance, merge a PR, or broaden the scope. Phase 1 is docs-only and must leave exactly one repository file changed. Phase 2 starts only after Phase 1 is merged. Phase 3 stops before merge even with green CI and dual-lane approval.

## Ownership Seams

`MEMORY.md` and `USER.md` are owned by `memory_tool` and user-facing memory writes. Chroma `agent_memories` is owned by the memory provider and is read-only for G1A. `run_agent.py` owns prompt assembly and suppression policy. `plugins/memory/chromadb/__init__.py` owns provider retrieval, scoring, fallback, receipts, and provider block construction. `plugins/memory/chromadb/prompt_profile.py` owns deterministic generated-profile ranking and rendering. `/Users/jeremiah/.hermes/config.yaml` owns the user-visible kill switch. `~/.hermes/logs/boot_synthesis.jsonl` owns observability receipts. No lane may redefine a seam owned by another layer.

## Worktree / Branch Strategy

Phase 1 uses branch `feat/memory-g1a-contract` in `/Users/jeremiah/.hermes/hermes-agent` and creates only `docs/memory/G1A_CONTRACT.md`. Phase 2 must use a separate implementation branch after the contract PR is merged. The controller alone may run `git fetch`, `git pull`, `git merge`, `git rebase`, branch deletion, or PR merge commands. Worktrees are optional for Phase 2, but if used, each lane must have a distinct worktree and may not mutate the same checkout concurrently.

## Controller Operations

Before each phase, the controller runs preflight checks for git state, scope, dependency reachability, and reviewer-lane availability. During each phase, the controller dispatches Codex GPT-5.5 and Claude Opus 4.7 as independent lanes, reconciles divergences, and records the disposition in the PR or final status. After edits, the controller verifies the exact changed-file set, runs the plan evaluator or documented fallback, runs both read-only reviews on the final diff, commits, pushes, and opens the PR. The controller stops before merge unless explicit authorization is provided.

## Existing code anchors

These are the contract anchors for implementation parity checks:

- `agent/memory_manager.py:283` — `MemoryManager.external_system_prompt_block()` exposes the provider block to core.
- `run_agent.py:6144` — `_build_system_prompt_parts()` assembles the system prompt.
- `run_agent.py:6325` — core calls `self._memory_manager.external_system_prompt_block()`.
- `run_agent.py:6336` — external block errors fall back to an empty provider block.
- `run_agent.py:6342` — legacy MEMORY/USER suppression decision starts here.
- `run_agent.py:6345` — degraded provider blocks remain additive and never suppress legacy memory.
- `plugins/memory/chromadb/__init__.py:152` — current semantic weight default is `0.5`.
- `plugins/memory/chromadb/__init__.py:153` — current recency weight default is `0.3`.
- `plugins/memory/chromadb/__init__.py:154` — current importance weight default is `0.2`.
- `plugins/memory/chromadb/__init__.py:410` — generated profile query assembly begins in the current provider path.
- `plugins/memory/chromadb/__init__.py:534` — current memory block budget is `2200` chars.
- `plugins/memory/chromadb/__init__.py:612` — `builtin_mirror` source is written for add operations.
- `plugins/memory/chromadb/__init__.py:614` — `builtin_mirror` source is written for replace operations.
- `plugins/memory/chromadb/__init__.py:824` — `pre_compress_extraction` source is written by pre-compress extraction.
- `plugins/memory/chromadb/__init__.py:1352` — current `_score_results()` starts.
- `plugins/memory/chromadb/__init__.py:1355` — current recency window is `30 * 24 * 3600` seconds.
- `plugins/memory/chromadb/__init__.py:1379` — current three-signal composite formula starts.
- `plugins/memory/chromadb/prompt_profile.py:117` — generated profile renderer has matching current `0.5 / 0.3 / 0.2` local weights.
- `plugins/memory/chromadb/config.py:31` — provider-local generated profile config starts.
- `plugins/memory/chromadb/config.py:50` — current user budget is `1375` chars.
- `plugins/memory/chromadb/config.py:51` — current memory budget is `2200` chars.
- `plugins/memory/chromadb/config.py:114` — provider config currently stores the three scoring weights.
- `tools/memory_tool.py:194` — memory tool persistence path starts.
- `tools/memory_tool.py:431` — MEMORY.md / USER.md atomic file writer starts.
- `tools/memory_tool.py:582` — memory writes notify provider mirrors.

## Architectural decision

The boot memory artifact is the Chroma provider block returned through `MemoryManager.external_system_prompt_block()` and inserted by `_build_system_prompt_parts()`.

The implementation PR must not create a new boot memory file, must not overwrite `MEMORY.md`, and must not overwrite `USER.md`.

`MEMORY.md` and `USER.md` remain the curated flat-file source of truth for user-visible `memory_tool` writes. Chroma `builtin_mirror` facts are downstream mirrors of those curated writes. The synthesized Chroma block lives alongside the curated flat-file system-prompt memory according to the existing prompt-source policy:

- In `prompt_source=provider`, a non-degraded provider block replaces legacy MEMORY/USER prompt injection.
- In degraded or fallback cases, the provider block is additive or empty according to the existing code path; degraded blocks do not suppress legacy memory.
- The implementation must not alter `memory_tool` write semantics, flat-file format, or `builtin_mirror` mirroring semantics.

## Behavior changes for Phase 2 implementation

### Salience formula

Replace the current three-signal `_score_results()` formula:

- `0.5` semantic similarity.
- `0.3` recency.
- `0.2` importance.

with the static G1A v1 five-signal formula:

- `0.35` semantic similarity.
- `0.25` recency.
- `0.20` source_quality.
- `0.15` importance.
- `0.05` durability.

The weights intentionally sum to `1.00`.

Recency keeps the existing `30` day linear window from `plugins/memory/chromadb/__init__.py:1355`.

The same scoring semantics must apply to the generated profile renderer path in `plugins/memory/chromadb/prompt_profile.py:117` so provider scoring and boot-profile rendering cannot drift.

### source_quality signal

`source_quality` is derived from existing Chroma metadata, primarily the `source` key.

Canonical descending order:

1. `builtin_mirror` and hand-authored MEMORY/USER flat-file equivalents: highest quality.
2. `seed`: high quality.
3. `pre_compress_extraction`: medium quality.
4. `session_turn`: lowest quality.
5. missing or unknown `source`: lowest quality, tied with `session_turn`, and included in receipts as `source="<missing>"` or the observed raw value.

Clarification from dual-lane plan gate: `seed` appears in live Chroma data but is not emitted by a current writer in the inspected code path; `session_turn` is treated as a legacy/possible data class. The implementation must honor both on read and must not rewrite existing rows to normalize them.

Current live read-only distribution at Phase 1 preflight:

- `agent_memories` count: `2294` docs.
- `source` distribution: `pre_compress_extraction=1871`, `builtin_mirror=398`, `seed=16`, missing source `9`.
- `target` distribution: `memory=2100`, `user=190`, missing target `4`.

The earlier recon count was `2286` docs. The `2286 -> 2294` delta is classified as acceptable live-data drift because Chroma writers can add rows between observation windows. For this contract, drift of less than `1%` of the corpus within a `24` hour window is not a contradiction. A new source class or a drift greater than `10%` on any source class before implementation is a pivot condition requiring recon before code changes.

### durability signal

The implementation must classify candidate facts into exactly one durability label:

- `durable`: long-term user preferences, identity/profile facts, stable environment facts, legal/profile constraints, durable corrections, or stable project conventions.
- `time-bound`: applications, deals, in-flight projects, active opportunities, temporary infrastructure windows, or commitments with implied expiration.
- `ephemeral`: task progress, PR numbers, commit SHAs, transient operational state, temporary TODO status, stale session progress, and one-off status updates.

Selection policy:

- `durable` facts are favored.
- `ephemeral` facts are excluded from synthesized output.
- `time-bound` facts are included only inside their validity window.

Classifier contract:

- The Phase 2 implementation may use deterministic heuristics first, optionally assisted by synthesis-time model classification, but the final label must be available in the receipt as `durability_label`.
- If a Chroma row lacks metadata for validity windows, a `time-bound` candidate is stale unless the classifier can infer a valid current window from metadata or content.
- The implementation must never write durability labels back into Chroma during G1A. Labels are computed on read and logged in receipts only.

### Deduplication

Before synthesis, candidates must be deduplicated by two mechanisms:

1. Exact normalized content hash:
   - Normalize by Unicode NFKC, lowercase, trim, collapse whitespace, and remove volatile punctuation-only differences.
   - Hash with SHA-256.
   - Identical normalized hashes collapse to one representative.

2. Embedding near-duplicate detection:
   - Use cosine similarity over candidate embeddings.
   - Threshold: `0.92` cosine similarity or greater means near-duplicate.
   - SpaceX-class and Anduril-class near-duplicates must collapse to one representative.

Representative selection order:

1. Highest `source_quality`.
2. Highest `importance`.
3. Highest `durability` preference, with `durable > time-bound > ephemeral` and `ephemeral` excluded before final selection.
4. Highest recency.
5. Highest semantic similarity.

Receipts must include dropped duplicate IDs with reason `duplicate` or `superseded` as applicable.

### Synthesis model and timeout

The synthesis model is `qwen2.5:7b` via local Ollama on the Mac.

Hard timeout: `8` seconds.

The `8` second timeout is justified against the recon-measured `6.18` second baseline: it gives approximately `1.82` seconds of headroom while keeping boot latency bounded.

If synthesis times out, returns empty output, returns unsafe output, or Ollama is unreachable, Hermes must boot successfully through the legacy non-synthesized Chroma block path. Fallback must be logged in the receipt and must not crash the session.

### Output target and budgets

The output target remains Chroma `external_system_prompt_block` at the existing `_build_system_prompt_parts()` call site.

Budgets unchanged:

- User/profile cap: `1375` chars.
- Memory block cap: `2200` chars.
- The G1A synthesized boot block must be `<= 2200` chars.

The implementation must not modify upstream caps.

### Kill switch

Add a config flag:

```yaml
memory:
  boot_synthesis_enabled: true
```

Location: `/Users/jeremiah/.hermes/config.yaml`.

Default: `true`.

This is intentionally a core Hermes YAML flag, not a provider-local `chromadb.json` flag. Phase 2 must bridge the YAML value into Chroma provider initialization rather than requiring users to edit `$HERMES_HOME/chromadb.json`.

When `memory.boot_synthesis_enabled=false`, behavior must be bit-identical to the pre-change non-synthesized Chroma block path.

Rollback command:

```bash
hermes config set memory.boot_synthesis_enabled false
```

If the CLI does not support this nested write at implementation time, the fallback rollback command is:

```bash
python3 - <<'PY'
from pathlib import Path
import yaml
p = Path('/Users/jeremiah/.hermes/config.yaml')
data = yaml.safe_load(p.read_text()) or {}
data.setdefault('memory', {})['boot_synthesis_enabled'] = False
p.write_text(yaml.safe_dump(data, sort_keys=False))
PY
```

Rollback takes effect on the next Hermes session boot. No code revert, Chroma change, Forge change, Sentinel restart, or deployment is required.

## Receipt contract

Every boot synthesis run must append exactly one JSON object to:

```text
~/.hermes/logs/boot_synthesis.jsonl
```

Required fields:

- `timestamp`: UTC ISO-8601 timestamp with millisecond precision.
- `session_id`: current Hermes session ID.
- `platform`: `cli`, gateway platform name, or other runtime platform value.
- `gateway_session_key`: gateway session key when available, otherwise `null`.
- `query_strings`: list of query strings used.
- `collections_searched`: list of Chroma collections searched.
- `candidates`: list of objects containing candidate `id`, raw score components, composite score, source metadata, target metadata, `stored_at`, `importance`, and `durability_label`.
- `selected_ids`: list of IDs that survived into synthesized output.
- `dropped_ids`: list of objects `{id, reason}`.
- `pre_dedup_count`: integer candidate count before dedup.
- `post_dedup_count`: integer candidate count after dedup.
- `model`: synthesis model used or attempted. Expected value is `qwen2.5:7b` when synthesis is attempted, including timeout/model fallback cases; expected value is `legacy` only when synthesis is intentionally not attempted because `memory.boot_synthesis_enabled=false`.
- `input_chars`: synthesis prompt input character count, or `0` when synthesis is intentionally not attempted.
- `output_chars`: synthesized output character count, or legacy fallback output character count when fallback emits the legacy block.
- `latency_ms`: end-to-end synthesis latency in milliseconds, or fallback path latency when synthesis is intentionally not attempted.
- `fallback_path_taken`: boolean.
- `fallback_reason`: one of `null`, `timeout`, `chroma_unreachable`, `model_unreachable`, `empty_output`, `unsafe_output`, `kill_switch_off`, `exception`.
- `output_sha256`: SHA-256 of the emitted boot block content.
- `previous_block_sha256`: SHA-256 of the previous emitted boot block found from the most recent prior receipt when available, otherwise `null`.
- `diff_summary`: compact summary of line changes against `previous_block_sha256` content when the previous block can be reconstructed or cached, otherwise `null`.

Allowed `dropped_ids[].reason` values:

- `duplicate`
- `over_budget`
- `low_score`
- `stale`
- `unsafe`
- `superseded`
- `ephemeral`
- `out_of_validity_window`

Receipt safety requirements:

- Receipts must not include raw embedding vectors.
- Receipts must be valid JSONL.
- A boot must append at most one receipt.
- A boot must append a receipt even when fallback is taken.
- Receipt write failure must not crash Hermes, but it must be logged through normal application logging.

## Expected behavior assertions

Phase 2 pytest and smoke coverage must prove every assertion below.

A1. With `memory.boot_synthesis_enabled=true`, Chroma reachable, and Ollama reachable, a fresh session produces a non-empty Chroma boot block within `8` seconds and the block is `<= 2200` chars.

A2. With `memory.boot_synthesis_enabled=true` and Chroma unreachable, the session boots successfully using the legacy non-synthesized Chroma block path. A receipt is appended with `fallback_path_taken=true` and `fallback_reason="chroma_unreachable"` or `fallback_reason="exception"`.

A3. With `memory.boot_synthesis_enabled=true` and Ollama unreachable, the session boots successfully using the legacy non-synthesized Chroma block path. A receipt is appended with `fallback_path_taken=true` and `fallback_reason="model_unreachable"`.

A4. With `memory.boot_synthesis_enabled=false`, behavior is bit-identical to the pre-change Chroma block path. The implementation must verify this by hashing the assembled provider block or assembled system prompt under a controlled fixture.

A5. The receipt file is appended exactly once per session boot, is valid JSONL, and every required field is populated.

A6. `MEMORY.md` and `USER.md` byte-content are unchanged across a boot synthesis run. The implementation must verify pre/post SHA-256 equality.

A7. Given two candidates with identical normalized content, at most one survives into synthesized output.

A8. Given two candidates with embedding cosine similarity `>= 0.92`, at most one survives into synthesized output unless a test explicitly proves the threshold would be a false positive for that pair.

A9. Candidates labeled `ephemeral` never appear in synthesized output.

A10. `time-bound` candidates outside their validity window never appear in synthesized output.

A11. Fallback paths do not suppress legacy memory unless the existing pre-change prompt-source policy would have suppressed it.

## Worked-example reachability proof required for Phase 2

Phase 2 must include a manifest smoke test against the live `agent_memories` collection that demonstrates, not merely asserts:

1. A fresh session produces a synthesized boot block `<= 2200` chars when `memory.boot_synthesis_enabled=true` and dependencies are reachable.
2. The receipt for that boot contains every required field from this contract.
3. At least one durable `target=user` fact survives selection.
4. SpaceX-class or Anduril-class near-duplicates collapse through the dedup pass.
5. The receipt lists duplicate dropped IDs with reason `duplicate` or `superseded`.
6. `MEMORY.md` and `USER.md` SHA-256 hashes are unchanged before and after the run.

Live preflight reference:

- Recon count: `2286` docs at the earlier observation time.
- Phase 1 preflight count: `2294` docs.
- Source distribution: `pre_compress_extraction=1871`, `builtin_mirror=398`, `seed=16`, missing source `9`.
- Target distribution: `memory=2100`, `user=190`, missing target `4`.

The count drift is acceptable live-data drift for Phase 1. It becomes a pivot only if the implementation preflight sees new source classes, a corpus count drop below `2000`, or source-class drift greater than `10%`.

## Non-claims and deferred goals

This goal does not enforce first-turn semantic recall for high-risk intents. That is Goal 2 and is the full fix for the Anduril-class failure documented in session `20260521_133818_46a983`.

This goal does not add intent classification or task-routing for retrieval. That is Goal 3.

This goal does not add per-fact access counters or correction-feedback signals. That is Goal 1B and informs future salience tuning.

This goal does not make salience weights dynamic based on request complexity. Static v1 weights are intentional until Goal 1B observability exists.

This goal does not modify the upstream `2200` / `1375` character caps.

This goal does not modify `MEMORY.md` or `USER.md` writes, `memory_tool` semantics, or the `builtin_mirror` Chroma bridge.

## Pivot conditions

Re-open the contract rather than patching silently if any of the following happen before or during Phase 2:

- ChromaDB at `100.107.68.104:8000` is unreachable from the Mac for an extended testing window.
- Forge embedding service at `100.113.1.2:8006` is unreachable when embedding dedup is tested.
- `qwen2.5:7b` through local Ollama cannot meet the `8` second synthesis timeout reliably.
- Actual source distribution contradicts the `source_quality` ordering; examples include a new high-volume source class or more than `10%` drift in any class.
- The live `agent_memories` corpus drops below `2000` docs.
- Dedup cannot reliably collapse SpaceX/Anduril-class duplicates at `0.92` cosine without false positives.
- Receipts cannot be written exactly once per boot without risking boot stability.
- Any implementation path requires modifying `MEMORY.md`, `USER.md`, Sentinel state, Forge state, or another Hermes instance.

## Rollback

Primary rollback:

```bash
hermes config set memory.boot_synthesis_enabled false
```

Fallback rollback if nested config write support is unavailable:

```bash
python3 - <<'PY'
from pathlib import Path
import yaml
p = Path('/Users/jeremiah/.hermes/config.yaml')
data = yaml.safe_load(p.read_text()) or {}
data.setdefault('memory', {})['boot_synthesis_enabled'] = False
p.write_text(yaml.safe_dump(data, sort_keys=False))
PY
```

Then start a fresh Hermes session. No code revert is required.

## Decision Rules

If both Codex GPT-5.5 and Claude Opus 4.7 approve a gate and local verification passes, the controller may advance to the next non-merge phase within the user-authorized scope. If either reviewer returns `REQUEST_CHANGES`, the controller must treat it as an active blocker, repair or revise the artifact, and rerun both lanes on the exact updated scope. If reviewers disagree after one repair loop, or if any lane proposes Sentinel/Forge writes, flat-file memory edits, service restarts, deployment, or merge, the controller must stop and surface the decision. If dependency reachability contradicts this contract, the controller re-opens the contract rather than silently patching implementation behavior.

## Phase gates

Each phase gate is a hard acceptance boundary. A phase may advance only when the named artifacts exist, the changed-file set is inside scope, plan evaluation or its documented fallback passes, both independent review lanes approve the same artifact scope, and the controller has recorded any discrepancies. Green tests or a single reviewer approval are never sufficient. Merge remains a separate explicit authorization gate.

### Phase 1: contract PR

Required before opening the PR:

- Codex GPT-5.5 read-only plan lane returns approval or all requested changes are resolved.
- Claude Opus 4.7 read-only plan lane returns approval or all requested changes are resolved.
- `docs/memory/G1A_CONTRACT.md` is the only repo file changed.
- Plan evaluator reports pass, completeness `1.0`, and zero deficiencies, or the controller records the exact local evaluator fallback used and why it is equivalent for this docs-only artifact.
- Codex GPT-5.5 and Claude Opus 4.7 both review the final docs-only diff.
- Stop before merge unless explicit controller authorization is given.

### Phase 2: implementation PR

Phase 2 must start only after Phase 1 is merged. It must implement exactly this contract and include:

- Modified `_score_results()` with the five-signal formula.
- Matching generated-profile renderer scoring updates.
- `source_quality` and durability classifiers.
- Pre-synthesis dedup pass using content-hash and `0.92` cosine similarity threshold.
- `qwen2.5:7b` local Ollama synthesis with `8` second timeout and fallback.
- Receipt writer to `~/.hermes/logs/boot_synthesis.jsonl`.
- `memory.boot_synthesis_enabled` config flag with default `true` and provider init bridge.
- Pytest coverage for A1 through A11.
- Manifest smoke test against live ChromaDB, read-only.

Both Codex GPT-5.5 and Claude Opus 4.7 must approve before Phase 2 is considered stable. Stop before merge.

### Phase 3: final review and stop-before-merge

After Phase 2 CI is green:

- Codex GPT-5.5 performs full review.
- Claude Opus 4.7 performs full review.
- Contract-to-implementation parity is verified.
- Mac-only scope is verified.
- Pytest coverage is mapped to every assertion A1-A11.
- Rollback is verified end-to-end.
- Receipt schema is validated.
- `MEMORY.md` and `USER.md` byte-content are verified unchanged.
- Both reviewer reports are surfaced to the controller.
- Implementation PR remains unmerged pending explicit controller authorization.

## Completion criteria for the overall G1A goal

G1A is complete only when:

- The Phase 1 contract PR is merged with both-lane approval and plan evaluation pass.
- The Phase 2 implementation PR has both-lane approval and green CI.
- The Phase 2 implementation PR is not merged and is awaiting explicit controller authorization.
- Both final reviewer reports are surfaced in a single completion message.
- `docs/memory/G1A_STATUS.md` is appended with what was built, what was deferred to Goal 1B / Goal 2 / Goal 3, and any decisions warranting controller review.

A judge must not mark G1A complete on single-lane approval, on a merged implementation PR, on deviation from Mac-only scope, or on `MEMORY.md` / `USER.md` content changes.
