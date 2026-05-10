# ChromaDB Memory Plugin

ChromaDB-backed vector memory for Hermes Agent — provides semantic search,
composite scoring, team memory, and memory consolidation.

## Features

- **Semantic Search**: Query memories by meaning using embeddings
- **Composite Scoring**: `0.5*similarity + 0.3*recency + 0.2*importance`
- **7 Collections**: agent_memories, session_history, team_knowledge, team_ops, agent_rilo, agent_caddie, agent_scout
- **Team Memory**: `team_memory` tool for store_discovery / search_knowledge
- **Vector Search**: `vector_search` tool for direct semantic search
- **Memory Consolidation**: Auto-manages flat-file memory budget with importance scoring and merging
- **Write Mirroring**: Automatically mirrors built-in memory writes to ChromaDB
- **Delegation Tracking**: Stores subagent results in team_ops collection
- **Session History**: Stores session summaries for cross-session recall

## Embedding Fallback Chain

1. **Forge Service** (`http://100.113.1.2:8006`) — primary, fast, Qwen3 embeddings
2. **FastEmbed** — local fallback if Forge is unreachable
3. **ChromaDB Default** — last resort built-in embeddings

## Configuration

Config is stored in `$HERMES_HOME/chromadb.json`:

```json
{
  "enabled": true,
  "chromadb_host": "100.107.68.104",
  "chromadb_port": 8000,
  "embedding_service_url": "http://100.113.1.2:8006",
  "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
  "similarity_weight": 0.5,
  "recency_weight": 0.3,
  "importance_weight": 0.2,
  "default_char_budget": 2200
}
```

Or use `hermes memory setup` to configure interactively.

## Activation

In `config.yaml`:

```yaml
memory:
  provider: chromadb
```

## Architecture

This plugin implements the `MemoryProvider` ABC from `agent/memory_provider.py`.
It is loaded by the plugin discovery system in `plugins/memory/__init__.py` and
orchestrated by `agent/memory_manager.py`.

Ported from:
- `tools/vector_memory.py` (VectorMemoryProvider, ForgeEmbeddingFunction)
- `tools/vector_memory_config.py` (VectorMemoryConfig)
- `tools/vector_write_tool.py` (team_memory tool)
- `tools/memory_consolidation.py` (MemoryConsolidator)
