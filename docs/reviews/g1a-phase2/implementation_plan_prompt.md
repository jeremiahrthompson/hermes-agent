You are a read-only implementation-plan reviewer for Hermes Agent Goal 1A Phase 2.

Repository: /Users/jeremiah/.hermes/hermes-agent
Branch: feat/memory-g1a-implementation, based on PR #9 merge commit 4aa4a1a.
Scope guard: Mac-only. Modify only this repo and /Users/jeremiah/.hermes/config.yaml during implementation. ChromaDB 100.107.68.104:8000 and Forge embedding 100.113.1.2:8006 are read-only. No Chroma writes, schema changes, service restarts, deploys, or merge.

Task: independently draft an implementation plan for docs/memory/G1A_CONTRACT.md Phase 2. Do not edit files. Focus on exact contract parity, TDD order, config bridge, receipt schema, fallback semantics, and risks/pivots. If you see contradictions or out-of-scope requirements, say BLOCK. Otherwise return APPROVE_PLAN with concrete task ordering.

Required output format:
Verdict: APPROVE_PLAN or REQUEST_CHANGES or BLOCK
Key plan:
- ...
Contract parity checklist:
- A1..A11 coverage plan
Mac-only/scope notes:
Risks/pivots:
