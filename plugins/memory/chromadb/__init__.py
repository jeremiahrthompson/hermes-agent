"""ChromaDB memory plugin — MemoryProvider for ChromaDB-backed vector memory.

Provides semantic search, composite scoring (similarity + recency + importance),
team memory (store_discovery / search_knowledge), multi-agent collection
segregation, memory consolidation, and session history.

The 7 collections:
  - agent_memories: core agent memories (mirrors flat-file writes)
  - session_history: session summaries
  - team_knowledge: shared infra, conventions, architecture
  - team_ops: cross-agent coordination state
  - agent_rilo, agent_caddie, agent_scout: per-agent private memory

Config: $HERMES_HOME/chromadb.json (profile-scoped)

Ported from:
  - tools/vector_memory.py (VectorMemoryProvider, ForgeEmbeddingFunction)
  - tools/vector_memory_config.py (VectorMemoryConfig)
  - tools/vector_write_tool.py (team_memory tool)
  - tools/memory_consolidation.py (MemoryConsolidator)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


_no_embedding_function_failure_count = 0


def get_no_embedding_function_failure_count() -> int:
    """Return how many times _embed() has been refused due to no EF available.

    Monotonically increasing for the lifetime of the process. Reset by
    restart only. Use for monitoring/alerting: a non-zero (and especially
    growing) count means memory writes/reads are being silently dropped
    because the embedding backend is unreachable.
    """
    return _no_embedding_function_failure_count


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TEAM_MEMORY_SCHEMA = {
    "name": "team_memory",
    "description": (
        "Store discoveries or search shared knowledge in team memory. "
        "Use store_discovery to save infrastructure findings, API quirks, "
        "conventions, or lessons learned. Use search_knowledge to find "
        "relevant past discoveries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["store_discovery", "search_knowledge"],
                "description": (
                    "Action to perform. 'store_discovery' saves content to a "
                    "collection. 'search_knowledge' searches for relevant entries."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "The discovery text to store (required for store_discovery)."
                ),
            },
            "collection": {
                "type": "string",
                "enum": ["team_knowledge", "team_ops", "own"],
                "description": (
                    "Target collection. 'team_knowledge' for shared infra/conventions, "
                    "'team_ops' for cross-agent coordination, 'own' for agent-private "
                    "memories. Defaults to 'team_knowledge'."
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "Search query text (required for search_knowledge)."
                ),
            },
        },
        "required": ["action"],
    },
}

VECTOR_SEARCH_SCHEMA = {
    "name": "vector_search",
    "description": (
        "Semantic search over ChromaDB vector memory. Searches agent memories, "
        "session history, or team knowledge by meaning, not just keywords. "
        "Returns scored results ranked by composite relevance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in vector memory.",
            },
            "collection": {
                "type": "string",
                "enum": ["memories", "sessions", "team_knowledge", "team_ops", "all"],
                "description": (
                    "Which collection to search. 'all' searches across accessible "
                    "collections. Defaults to 'memories'."
                ),
            },
            "n_results": {
                "type": "integer",
                "description": "Maximum number of results (default: 5, max: 20).",
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class ChromaDBMemoryProvider(MemoryProvider):
    """ChromaDB-backed vector memory with semantic search, composite scoring,
    team memory, and memory consolidation."""

    def __init__(self):
        self._client = None
        self._collections: Dict[str, Any] = {}
        self._available = False
        self._config = None
        self._session_id = ""
        self._ef = None  # ForgeEmbeddingFunction, set in initialize()
        self._hermes_home = ""
        self._agent_name = "rilo"
        self._team_context = ""

        # Scoring weights (set from config during initialize)
        self._w_sim = 0.5
        self._w_rec = 0.3
        self._w_imp = 0.2

        # Threading
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None

        # Consolidator (lazy init)
        self._consolidator = None

        # Cron guard
        self._cron_skipped = False

        # Phase 1 / Lane B: generated profile prompt transport. Core sets
        # these via initialize(**kwargs); the plugin never reads
        # hermes_cli.config directly.
        self._prompt_source: str = "legacy"
        self._generated_profile_enabled: bool = False
        self._agent_context: str = "primary"

    @property
    def name(self) -> str:
        return "chromadb"

    # -- Availability --------------------------------------------------------

    def is_available(self) -> bool:
        """Check if ChromaDB plugin is configured. No network calls."""
        try:
            import chromadb  # noqa: F401
            return True
        except ImportError:
            return False

    # -- Config schema for 'hermes memory setup' ----------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "chromadb_host",
                "description": "ChromaDB server host (IP or hostname)",
                "default": "100.107.68.104",
                "required": True,
            },
            {
                "key": "chromadb_port",
                "description": "ChromaDB server port",
                "default": "8000",
            },
            {
                "key": "embedding_service_url",
                "description": "Forge embedding service URL",
                "default": "http://100.113.1.2:8006",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Write config to $HERMES_HOME/chromadb.json."""
        from pathlib import Path
        config_path = Path(hermes_home) / "chromadb.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    # -- Initialize ----------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize ChromaDB client, collections, and team context."""
        try:
            # Cron guard
            agent_context = kwargs.get("agent_context", "")
            platform = kwargs.get("platform", "cli")
            if agent_context in ("cron", "flush") or platform == "cron":
                logger.debug("ChromaDB skipped: cron/flush context")
                self._cron_skipped = True
                return

            self._session_id = session_id
            self._hermes_home = kwargs.get("hermes_home", "")
            self._agent_name = kwargs.get("agent_identity", "rilo")
            # Phase 1 transport: core tells the plugin which prompt-source
            # mode is active and whether generated profile is enabled, via
            # initialize kwargs.  The plugin must not import core config.
            self._agent_context = str(agent_context or "primary")
            self._prompt_source = str(kwargs.get("prompt_source", "legacy") or "legacy")
            self._generated_profile_enabled = bool(
                kwargs.get("generated_prompt_enabled", False)
            )

            # Load config
            from plugins.memory.chromadb.config import ChromaDBConfig
            if self._hermes_home:
                self._config = ChromaDBConfig.from_json_file(self._hermes_home)
            else:
                self._config = ChromaDBConfig()

            self._config.agent_name = self._agent_name
            self._w_sim = self._config.similarity_weight
            self._w_rec = self._config.recency_weight
            self._w_imp = self._config.importance_weight

            # Init ChromaDB client
            self._init_client()

            if not self._available:
                logger.debug("ChromaDB not available after init — plugin inactive")
                return

            # Pre-load team knowledge for system prompt injection
            self._load_team_context()

        except Exception as e:
            logger.warning("ChromaDB init failed: %s", e)
            self._available = False

    def _init_client(self) -> None:
        """Initialize ChromaDB client and all 7 collections."""
        try:
            import chromadb

            self._client = chromadb.HttpClient(
                host=self._config.chromadb_host,
                port=self._config.chromadb_port,
            )

            # Set up embedding function for manual embedding.
            # ChromaDB v1.5+ persists the EF config and rejects a different one
            # at open time, so we open collections WITHOUT an EF and embed
            # documents ourselves via ForgeEmbeddingFunction before add/query.
            from plugins.memory.chromadb.embedding import get_embedding_function
            self._ef = get_embedding_function(
                self._config.embedding_service_url,
                self._config.embedding_model,
                fallback_enabled=self._config.embedding_fallback_enabled,
                fallback_url=self._config.embedding_fallback_url,
            )

            # Open all configured collections without specifying an EF
            for key, col_name in self._config.collections.items():
                self._collections[key] = self._client.get_or_create_collection(
                    name=col_name
                )

            # Ensure the current agent's own collection exists even if it
            # wasn't listed in the static config (e.g. profile != "rilo").
            _own_key = f"agent_{self._agent_name}"
            if _own_key not in self._collections:
                self._collections[_own_key] = self._client.get_or_create_collection(
                    name=_own_key
                )
                logger.info("Created dynamic collection '%s' for agent identity", _own_key)

            self._available = True
            logger.info(
                "ChromaDB connected at %s:%s (%d collections)",
                self._config.chromadb_host,
                self._config.chromadb_port,
                len(self._collections),
            )
        except Exception as e:
            self._available = False
            logger.warning(
                "ChromaDB unavailable (%s). Vector memory will return empty results.", e
            )

    def _load_team_context(self) -> None:
        """Pre-load team knowledge for system prompt injection."""
        if not self._available:
            return
        try:
            results = self.search_team_knowledge(
                "infrastructure conventions architecture workflow", n_results=10
            )
            if results:
                scored = self._score_results(results)
                scored.sort(key=lambda x: x["composite_score"], reverse=True)
                entries = []
                total = 0
                for item in scored[:8]:
                    content = item["content"]
                    if total + len(content) > 1500:
                        break
                    entries.append(f"- {content}")
                    total += len(content)
                if entries:
                    self._team_context = "\n".join(entries)
        except Exception as e:
            logger.debug("Failed to load team context: %s", e)

    # -- System prompt block ------------------------------------------------

    def system_prompt_block(self) -> str:
        """Return team knowledge context for the system prompt.

        Phase 1 (Lane B): may additionally append a bounded
        ``<memory-profile>`` block generated from vector memory when
        ``prompt_source`` selects ``provider_with_legacy_fallback`` or
        ``provider``.  In ``shadow`` mode the block is generated and cached
        but NOT included in the returned text — operators get the
        cache/debug artifact without changing the live prompt.

        Team-knowledge injection is invariant across all prompt-source
        modes (plan §"Generated Prompt Block Contract").
        """
        if self._cron_skipped or not self._available:
            return ""

        parts = []
        parts.append(
            "# ChromaDB Vector Memory\n"
            "Active. Semantic search across 7 collections. Use team_memory and "
            "vector_search tools to store/retrieve knowledge."
        )

        if self._team_context:
            parts.append(f"\n## Team Knowledge\n{self._team_context}")

        generated = self._build_generated_profile_block()
        if generated:
            parts.append(generated)

        return "\n".join(parts)

    # -- Generated profile (Phase 1 / Lane B) -------------------------------

    def _generated_profile_should_run(self) -> bool:
        """Gate every condition described in the plan in one place."""
        if self._cron_skipped or not self._available:
            return False
        if not self._generated_profile_enabled:
            return False
        if self._prompt_source == "legacy":
            return False
        if self._agent_context in ("cron", "subagent", "flush"):
            return False
        if self._config is None:
            return False
        return True

    def _search_for_generated(self, target: str) -> List[Dict[str, Any]]:
        """Run a single ``_query()`` for the target and return ranked facts.

        Tests monkeypatch ``_query``/``_embed`` directly on the provider
        instance, so this method must go through ``self._query(...)`` and
        never call ``collection.query(...)`` directly.
        """
        from plugins.memory.chromadb.prompt_profile import rank_facts

        gp = self._config.generated_profile  # type: ignore[union-attr]
        if target == "user":
            query = gp.user_query
            n = max(1, int(gp.max_user_facts))
        else:
            query = gp.memory_query
            n = max(1, int(gp.max_memory_facts))

        collection = self._get_collection(target)
        if collection is None:
            return []
        try:
            raw = self._query(
                collection,
                query,
                n_results=n,
                where={"target": target},
            )
        except Exception as e:
            logger.debug("ChromaDB generated profile query for %s failed: %s", target, e)
            raise
        formatted = self._format_results(raw)
        return rank_facts(formatted, min_confidence=gp.min_confidence)

    def _build_generated_profile_block(self) -> str:
        """Generate, cache, and (optionally) return the ``<memory-profile>`` block.

        Returns empty string when the generated path should not run or
        when generation fails — never raises.  Cache writes happen even in
        ``shadow`` mode; cache reads/fallback are wired in Phase 2.
        """
        if not self._generated_profile_should_run():
            return ""

        try:
            from plugins.memory.chromadb.prompt_profile import (
                compute_cache_key,
                render_profile_block,
            )
            from plugins.memory.chromadb.prompt_cache import write_cache

            gp = self._config.generated_profile  # type: ignore[union-attr]

            user_facts = self._search_for_generated("user")
            memory_facts = self._search_for_generated("memory")

            collection_names = sorted((self._config.collections or {}).values())  # type: ignore[union-attr]
            selected_ids = [f.get("id", "") for f in user_facts + memory_facts]
            cache_key = compute_cache_key(
                profile=self._agent_name,
                collection_names=collection_names,
                selected_fact_ids=selected_ids,
                config_version=gp.config_version,
            )

            no_selected_facts = not user_facts and not memory_facts
            block, receipt = render_profile_block(
                user_facts=user_facts,
                memory_facts=memory_facts,
                max_user_chars=gp.max_user_chars,
                max_memory_chars=gp.max_memory_chars,
                cache_key=cache_key,
                degraded=no_selected_facts,
                include_debug_header=False,
            )

            if self._hermes_home:
                try:
                    write_cache(
                        self._hermes_home,
                        profile=self._agent_name,
                        cache_key=cache_key,
                        target="profile",
                        payload={
                            "block": block,
                            "receipt": receipt,
                            "generated_at": time.time(),
                        },
                    )
                except Exception as e:
                    logger.debug("ChromaDB cache write failed: %s", e)

            # Shadow mode: cache only, never alter the returned prompt block.
            if self._prompt_source == "shadow":
                return ""
            if self._prompt_source in ("provider_with_legacy_fallback", "provider"):
                return block
            return ""
        except Exception as e:
            # Any failure — embeddings unreachable, query backend down,
            # rendering error — downgrades the generated path to empty.
            # Legacy / team-knowledge text in system_prompt_block is
            # unaffected.
            logger.debug("ChromaDB generated profile failed: %s — degrading", e)
            return ""

    # -- Prefetch (semantic recall) -----------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return prefetched vector memory context from background thread."""
        if self._cron_skipped or not self._available:
            return ""

        # Wait for background prefetch if running
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)

        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""

        if not result:
            return ""

        return f"## Vector Memory Recall\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Fire a background semantic search for the next turn."""
        if self._cron_skipped or not self._available or not query:
            return

        def _run():
            try:
                # Search agent_memories for relevant context
                relevant = self.get_relevant_memories(
                    query, "memory",
                    char_budget=self._config.default_char_budget if self._config else 2200,
                )
                if relevant and relevant.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = relevant
            except Exception as e:
                logger.debug("ChromaDB prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="chromadb-prefetch"
        )
        self._prefetch_thread.start()

    # -- Sync turn ----------------------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Lightweight turn recording — queue for background processing."""
        if self._cron_skipped or not self._available:
            return

        # Keep lightweight: just record the turn in a background thread
        def _sync():
            try:
                # Store a compact turn record in session_history
                turn_text = f"User: {user_content[:500]}\nAssistant: {assistant_content[:500]}"
                turn_id = f"turn_{session_id or self._session_id}_{int(time.time())}"
                collection = self._collections.get("sessions")
                if collection:
                    self._upsert(collection, ids=[turn_id], documents=[turn_text],
                        metadatas=[self._sanitize_metadata({
                            "session_id": session_id or self._session_id,
                            "stored_at": time.time(),
                            "target": "session_turn",
                            "importance": 0.3,
                        })],
                    )
            except Exception as e:
                logger.debug("ChromaDB sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="chromadb-sync"
        )
        self._sync_thread.start()

    # -- Memory write mirror (Approach A) -----------------------------------

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes to ChromaDB."""
        if self._cron_skipped or not self._available or not content:
            return

        # Invalidate cached generated profile for the affected target so
        # the next session prompt build picks up fresh content (plan
        # global rule §14).  Best-effort — never raises.
        if target in ("user", "memory") and self._hermes_home:
            try:
                from plugins.memory.chromadb.prompt_cache import invalidate_target
                invalidate_target(
                    self._hermes_home,
                    profile=self._agent_name,
                    target=target,
                )
                # Generated profile caches combine user + memory facts into a
                # target="profile" artifact; any user/memory mutation makes
                # that combined artifact stale too.
                invalidate_target(
                    self._hermes_home,
                    profile=self._agent_name,
                    target="profile",
                )
            except Exception as e:
                logger.debug("ChromaDB cache invalidation failed: %s", e)

        def _write():
            try:
                if action == "add":
                    self.store_memory(content, target, {"source": "builtin_mirror"})
                elif action == "replace":
                    self.store_memory(content, target, {"source": "builtin_mirror"})
                elif action == "remove":
                    # Find and remove by content hash
                    doc_id = self._make_id(content, target)
                    self.remove_memory(doc_id, target)
            except Exception as e:
                logger.debug("ChromaDB memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="chromadb-memwrite")
        t.start()

    # -- Tool schemas -------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return team_memory and vector_search tool schemas.

        Tool routing is registered before provider initialization completes, so
        schemas must be exposed even while `_available` is still False. Runtime
        calls still fail closed in `handle_tool_call()` if ChromaDB is not ready.
        """
        if self._cron_skipped:
            return []
        return [TEAM_MEMORY_SCHEMA, VECTOR_SEARCH_SCHEMA]

    # -- Tool call routing --------------------------------------------------

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Route tool calls to team_memory or vector_search handlers."""
        if self._cron_skipped:
            return json.dumps({"error": "ChromaDB is not active (cron context)."})

        if not self._available:
            return json.dumps({"error": "ChromaDB is not available."})

        try:
            if tool_name == "team_memory":
                return self._handle_team_memory(args)
            elif tool_name == "vector_search":
                return self._handle_vector_search(args)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.error("ChromaDB tool %s failed: %s", tool_name, e)
            return json.dumps({"error": f"ChromaDB {tool_name} failed: {e}"})

    def _handle_team_memory(self, args: Dict[str, Any]) -> str:
        """Handle team_memory tool calls (store_discovery / search_knowledge)."""
        action = args.get("action", "")
        content = args.get("content", "")
        collection = args.get("collection", "team_knowledge")
        query = args.get("query", "")
        agent_name = self._agent_name

        if action == "store_discovery":
            if not content or not content.strip():
                return json.dumps({"status": "error", "error": "Content is required for store_discovery."})

            valid_collections = {"team_knowledge", "team_ops", "own"}
            if collection not in valid_collections:
                return json.dumps({"status": "error", "error": f"Invalid collection '{collection}'."})

            metadata = {"source_agent": agent_name}

            try:
                if collection == "team_knowledge":
                    memory_id = self.store_team_knowledge(content, metadata=metadata)
                elif collection == "team_ops":
                    memory_id = self.store_team_ops(content, agent_name=agent_name, metadata=metadata)
                elif collection == "own":
                    memory_id = self.store_agent_memory(content, agent_name=agent_name, metadata=metadata)
                else:
                    return json.dumps({"status": "error", "error": f"Unhandled collection '{collection}'."})

                if memory_id:
                    return json.dumps({
                        "status": "ok",
                        "memory_id": memory_id,
                        "collection": collection if collection != "own" else f"agent_{agent_name}",
                        "message": f"Discovery stored in {collection}.",
                    })
                else:
                    return json.dumps({"status": "error", "error": "Store returned empty ID."})

            except Exception as e:
                logger.warning("team_memory store_discovery failed: %s", e)
                return json.dumps({"status": "error", "error": f"Failed to store discovery: {e}"})

        elif action == "search_knowledge":
            if not query or not query.strip():
                return json.dumps({"status": "error", "error": "Query is required for search_knowledge."})

            try:
                results = []
                if collection == "own":
                    results = self.search_agent_memory(query, agent_name=agent_name, n_results=5)
                elif collection == "team_knowledge":
                    tk = self.search_team_knowledge(query, n_results=5)
                    own = self.search_agent_memory(query, agent_name=agent_name, n_results=5)
                    results = tk + own
                elif collection == "team_ops":
                    results = self.search_team_ops(query, n_results=5)
                else:
                    tk = self.search_team_knowledge(query, n_results=5)
                    own = self.search_agent_memory(query, agent_name=agent_name, n_results=5)
                    results = tk + own

                results.sort(key=lambda x: x.get("distance", 999.0))

                entries = []
                for r in results[:10]:
                    entries.append({
                        "content": r.get("content", ""),
                        "score": round(1.0 / (1.0 + r.get("distance", 1.0)), 4),
                        "metadata": r.get("metadata", {}),
                    })

                return json.dumps({"status": "ok", "results": entries, "count": len(entries)})

            except Exception as e:
                logger.warning("team_memory search_knowledge failed: %s", e)
                return json.dumps({"status": "error", "error": f"Search failed: {e}"})

        return json.dumps({"status": "error", "error": f"Unknown action '{action}'."})

    def _handle_vector_search(self, args: Dict[str, Any]) -> str:
        """Handle vector_search tool calls."""
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Missing required parameter: query"})

        collection = args.get("collection", "memories")
        n_results = min(int(args.get("n_results", 5)), 20)

        try:
            if collection == "all":
                results = self.search_all_accessible(query, agent_name=self._agent_name, n_results=n_results)
            elif collection == "memories":
                results = self.search_memories(query, "memory", n_results=n_results)
            elif collection == "sessions":
                results = self.search_sessions(query, n_results=n_results)
            elif collection == "team_knowledge":
                results = self.search_team_knowledge(query, n_results=n_results)
            elif collection == "team_ops":
                results = self.search_team_ops(query, n_results=n_results)
            else:
                results = self.search_memories(query, "memory", n_results=n_results)

            scored = self._score_results(results)
            scored.sort(key=lambda x: x["composite_score"], reverse=True)

            entries = []
            for r in scored[:n_results]:
                entries.append({
                    "content": r.get("content", ""),
                    "score": round(r.get("composite_score", 0), 4),
                    "similarity": round(r.get("similarity", 0), 4),
                    "metadata": r.get("metadata", {}),
                })

            return json.dumps({"status": "ok", "results": entries, "count": len(entries)})

        except Exception as e:
            logger.warning("vector_search failed: %s", e)
            return json.dumps({"error": f"Vector search failed: {e}"})

    # -- Delegation hook ----------------------------------------------------

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """Store subagent results in team_ops collection."""
        if self._cron_skipped or not self._available:
            return

        def _store():
            try:
                child_agent = kwargs.get("agent_identity", "subagent")
                content = f"[{child_agent}] Task: {task[:500]}\nResult: {result[:1000]}"
                self.store_team_ops(
                    content,
                    agent_name=child_agent,
                    metadata={
                        "parent_session": self._session_id,
                        "child_session": child_session_id,
                        "delegation_type": "subagent_result",
                    },
                )
            except Exception as e:
                logger.debug("ChromaDB on_delegation failed: %s", e)

        t = threading.Thread(target=_store, daemon=True, name="chromadb-delegation")
        t.start()

    # -- Pre-compress hook --------------------------------------------------

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Extract important facts from messages before context compression."""
        if self._cron_skipped or not self._available:
            return ""

        try:
            consolidator = self._get_consolidator()
            facts = consolidator.extract_facts_from_messages(messages)

            if not facts:
                return ""

            # Store extracted facts in ChromaDB
            for fact in facts:
                try:
                    self.store_memory(fact, "memory", {
                        "importance": 0.8,
                        "source": "pre_compress_extraction",
                    })
                except Exception:
                    pass

            return "Extracted facts preserved in vector memory:\n" + "\n".join(f"- {f}" for f in facts[:10])

        except Exception as e:
            logger.debug("ChromaDB on_pre_compress failed: %s", e)
            return ""

    # -- Session end hook ---------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Store session summary in session_history collection."""
        if self._cron_skipped or not self._available:
            return

        # Wait for pending sync
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)

        try:
            # Build a compact summary from messages
            summary_parts = []
            msg_count = 0
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if not content or not isinstance(content, str):
                    continue
                if role in ("user", "assistant"):
                    summary_parts.append(f"{role}: {content[:200]}")
                    msg_count += 1

            if not summary_parts:
                return

            summary = f"Session {self._session_id} ({msg_count} messages):\n"
            summary += "\n".join(summary_parts[:20])  # Cap at 20 turns
            summary = summary[:3000]  # Cap total length

            self.store_session_summary(
                self._session_id,
                summary,
                metadata={
                    "message_count": msg_count,
                    "agent_name": self._agent_name,
                },
            )
        except Exception as e:
            logger.debug("ChromaDB on_session_end failed: %s", e)

    # -- Shutdown -----------------------------------------------------------

    def shutdown(self) -> None:
        """Clean shutdown — wait for background threads."""
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        # ChromaDB HttpClient doesn't need explicit close, but clear references
        self._client = None
        self._collections = {}
        self._available = False

    # ======================================================================
    # Core vector memory methods (ported from VectorMemoryProvider)
    # ======================================================================

    def _get_collection(self, target: str):
        """Get the ChromaDB collection for a target type."""
        if target in ("memory", "user"):
            return self._collections.get("memories")
        elif target == "session":
            return self._collections.get("sessions")
        return self._collections.get("memories")

    def _embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts using the active embedding function (forge or OpenRouter).

        Raises RuntimeError if no embedding function is available — callers
        must not fall through to ChromaDB default/auto-embedding, which would
        produce 384-dim vectors incompatible with existing 1024-dim collections.
        """
        if self._ef is None:
            global _no_embedding_function_failure_count
            _no_embedding_function_failure_count += 1
            logger.error(
                "CHROMADB_EMBED_UNAVAILABLE: refusing to embed %d text(s) — "
                "no embedding provider reachable (forge + OpenRouter both failed). "
                "Memory write/read will be silently dropped by caller. "
                "Failure count this process: %d",
                len(texts), _no_embedding_function_failure_count,
            )
            raise RuntimeError(
                "No embedding provider available. Refusing to embed to protect "
                "existing 1024-dim collections from dimension mismatch."
            )
        return self._ef(texts)

    def _upsert(self, collection, ids, documents, metadatas=None):
        """Upsert with explicit embeddings. Fails closed if no EF available."""
        embeddings = self._embed(documents)  # raises if no EF
        kwargs = {"ids": ids, "documents": documents, "embeddings": embeddings}
        if metadatas:
            kwargs["metadatas"] = metadatas
        collection.upsert(**kwargs)

    def _query(self, collection, query_text: str, n_results: int = 10,
               where=None, include=None):
        """Query with explicit embeddings. Fails closed if no EF available."""
        if include is None:
            include = ["documents", "metadatas", "distances"]
        kwargs = {"n_results": n_results, "include": include}
        if where:
            kwargs["where"] = where
        embeddings = self._embed([query_text])  # raises if no EF
        kwargs["query_embeddings"] = embeddings
        return collection.query(**kwargs)

    @staticmethod
    def _make_id(content: str, target: str) -> str:
        """Generate a deterministic ID from content hash + target."""
        h = hashlib.sha256(f"{target}:{content}".encode()).hexdigest()[:16]
        return f"{target}_{h}"

    @staticmethod
    def _sanitize_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure metadata values are ChromaDB-compatible scalar types."""
        clean = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                clean[k] = v
            elif v is None:
                clean[k] = ""
            else:
                clean[k] = str(v)
        return clean

    # -- Store / search / remove memories -----------------------------------

    def store_memory(
        self, content: str, target: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Store a memory entry in ChromaDB."""
        if not self._available:
            return ""
        try:
            collection = self._get_collection(target)
            if collection is None:
                return ""

            meta = dict(metadata or {})
            meta["target"] = target
            meta["stored_at"] = time.time()
            meta.setdefault("importance", 0.5)
            meta = self._sanitize_metadata(meta)

            doc_id = self._make_id(content, target)
            self._upsert(collection, ids=[doc_id], documents=[content], metadatas=[meta])
            logger.debug("Stored memory %s in target=%s", doc_id, target)
            return doc_id
        except Exception as e:
            logger.warning("store_memory failed: %s", e)
            return ""

    def search_memories(
        self, query: str, target: str, n_results: int = 10
    ) -> List[Dict[str, Any]]:
        """Search memories by semantic similarity."""
        if not self._available:
            return []
        try:
            collection = self._get_collection(target)
            if collection is None:
                return []

            where_filter = {"target": target}
            results = self._query(collection, query, n_results=n_results, where=where_filter)
            return self._format_results(results)
        except Exception as e:
            logger.warning("search_memories failed: %s", e)
            return []

    def remove_memory(self, memory_id: str, target: str) -> bool:
        """Remove a memory by ID."""
        if not self._available:
            return False
        try:
            collection = self._get_collection(target)
            if collection is None:
                return False
            collection.delete(ids=[memory_id])
            logger.debug("Removed memory %s from target=%s", memory_id, target)
            return True
        except Exception as e:
            logger.warning("remove_memory failed: %s", e)
            return False

    def get_relevant_memories(
        self, query: str, target: str, char_budget: int = 2200
    ) -> str:
        """Get formatted memory text within char_budget, ranked by composite score."""
        if not self._available:
            return ""
        try:
            raw = self.search_memories(query, target, n_results=50)
            if not raw:
                return ""

            scored = self._score_results(raw)
            scored.sort(key=lambda x: x["composite_score"], reverse=True)

            entries = []
            total_chars = 0
            delimiter = "\n§\n"

            for item in scored:
                content = item["content"]
                needed = len(content) + (len(delimiter) if entries else 0)
                if total_chars + needed > char_budget:
                    continue
                entries.append(content)
                total_chars += needed

            if not entries:
                return ""
            return delimiter.join(entries)
        except Exception as e:
            logger.warning("get_relevant_memories failed: %s", e)
            return ""

    # -- Session summaries --------------------------------------------------

    def store_session_summary(
        self, session_id: str, summary: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Store a session summary."""
        if not self._available:
            return ""
        try:
            collection = self._collections.get("sessions")
            if collection is None:
                return ""

            meta = dict(metadata or {})
            meta["session_id"] = session_id
            meta["stored_at"] = time.time()
            meta["target"] = "session"
            meta.setdefault("importance", 0.5)
            meta = self._sanitize_metadata(meta)

            doc_id = f"session_{session_id}"
            self._upsert(collection, ids=[doc_id], documents=[summary], metadatas=[meta])
            logger.debug("Stored session summary %s", doc_id)
            return doc_id
        except Exception as e:
            logger.warning("store_session_summary failed: %s", e)
            return ""

    def search_sessions(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """Search session summaries by semantic similarity."""
        if not self._available:
            return []
        try:
            collection = self._collections.get("sessions")
            if collection is None:
                return []
            results = self._query(collection, query, n_results=n_results)
            return self._format_results(results)
        except Exception as e:
            logger.warning("search_sessions failed: %s", e)
            return []

    # -- Team knowledge / team ops / agent memory ---------------------------

    def store_team_knowledge(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Store shared team knowledge."""
        if not self._available:
            return ""
        try:
            collection = self._collections.get("team_knowledge")
            if collection is None:
                return ""
            meta = dict(metadata or {})
            meta["collection_type"] = "team_knowledge"
            meta["stored_at"] = time.time()
            meta.setdefault("importance", 0.5)
            meta = self._sanitize_metadata(meta)
            doc_id = self._make_id(content, "team_knowledge")
            self._upsert(collection, ids=[doc_id], documents=[content], metadatas=[meta])
            logger.debug("Stored team_knowledge %s", doc_id)
            return doc_id
        except Exception as e:
            logger.warning("store_team_knowledge failed: %s", e)
            return ""

    def search_team_knowledge(
        self, query: str, n_results: int = 10
    ) -> List[Dict[str, Any]]:
        """Search shared team knowledge."""
        if not self._available:
            return []
        try:
            collection = self._collections.get("team_knowledge")
            if collection is None:
                return []
            results = self._query(collection, query, n_results=n_results)
            return self._format_results(results)
        except Exception as e:
            logger.warning("search_team_knowledge failed: %s", e)
            return []

    def store_team_ops(
        self, content: str, agent_name: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Store team operational state."""
        if not self._available:
            return ""
        try:
            collection = self._collections.get("team_ops")
            if collection is None:
                return ""
            meta = dict(metadata or {})
            meta["collection_type"] = "team_ops"
            meta["agent_name"] = agent_name
            meta["stored_at"] = time.time()
            meta.setdefault("importance", 0.5)
            meta = self._sanitize_metadata(meta)
            doc_id = self._make_id(content, "team_ops")
            self._upsert(collection, ids=[doc_id], documents=[content], metadatas=[meta])
            logger.debug("Stored team_ops %s by agent %s", doc_id, agent_name)
            return doc_id
        except Exception as e:
            logger.warning("store_team_ops failed: %s", e)
            return ""

    def search_team_ops(
        self, query: str, agent_name: Optional[str] = None, n_results: int = 10
    ) -> List[Dict[str, Any]]:
        """Search team ops, optionally filtered by agent."""
        if not self._available:
            return []
        try:
            collection = self._collections.get("team_ops")
            if collection is None:
                return []
            where_filter = None
            if agent_name:
                where_filter = {"agent_name": agent_name}
            results = self._query(collection, query, n_results=n_results, where=where_filter)
            return self._format_results(results)
        except Exception as e:
            logger.warning("search_team_ops failed: %s", e)
            return []

    def store_agent_memory(
        self, content: str, agent_name: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Store memory in an agent-specific collection."""
        if not self._available:
            return ""
        try:
            col_key = f"agent_{agent_name}"
            collection = self._collections.get(col_key)
            if collection is None:
                return ""
            meta = dict(metadata or {})
            meta["collection_type"] = col_key
            meta["agent_name"] = agent_name
            meta["stored_at"] = time.time()
            meta.setdefault("importance", 0.5)
            meta = self._sanitize_metadata(meta)
            doc_id = self._make_id(content, col_key)
            self._upsert(collection, ids=[doc_id], documents=[content], metadatas=[meta])
            logger.debug("Stored agent memory %s for %s", doc_id, agent_name)
            return doc_id
        except Exception as e:
            logger.warning("store_agent_memory failed for %s: %s", agent_name, e)
            return ""

    def search_agent_memory(
        self, query: str, agent_name: str, n_results: int = 10
    ) -> List[Dict[str, Any]]:
        """Search an agent-specific memory collection."""
        if not self._available:
            return []
        try:
            col_key = f"agent_{agent_name}"
            collection = self._collections.get(col_key)
            if collection is None:
                return []
            results = self._query(collection, query, n_results=n_results)
            return self._format_results(results)
        except Exception as e:
            logger.warning("search_agent_memory failed for %s: %s", agent_name, e)
            return []

    def search_all_accessible(
        self, query: str, agent_name: str = "rilo", n_results: int = 10
    ) -> List[Dict[str, Any]]:
        """Search across all collections an agent can access."""
        if not self._available:
            return []
        try:
            if agent_name == "rilo":
                col_keys = [k for k in self._collections if k not in ("memories", "sessions")]
            else:
                col_keys = [f"agent_{agent_name}", "team_knowledge", "team_ops"]

            all_results = []
            for key in col_keys:
                collection = self._collections.get(key)
                if collection is None:
                    continue
                try:
                    raw = self._query(collection, query, n_results=n_results)
                    formatted = self._format_results(raw)
                    for r in formatted:
                        r["source_collection"] = key
                    all_results.extend(formatted)
                except Exception:
                    continue

            all_results.sort(key=lambda x: x.get("distance", 999.0))
            return all_results
        except Exception as e:
            logger.warning("search_all_accessible failed: %s", e)
            return []

    # -- Approach B: Smart memory ranking -----------------------------------

    def rank_and_select_entries(
        self,
        entries: List[str],
        target: str,
        char_budget: int = 2200,
        session_context: str = "",
    ) -> List[str]:
        """Rank flat-file entries using ChromaDB composite scores and select
        the best subset that fits within char_budget."""
        if not entries:
            return []

        if not self._available:
            return self._select_within_budget(entries, char_budget)

        try:
            query = session_context.strip() if session_context.strip() else self._default_context_query(target)
            raw = self.search_memories(query, target, n_results=50)
            if not raw:
                return self._select_within_budget(entries, char_budget)

            scored = self._score_results(raw)
            score_map: Dict[str, float] = {}
            for item in scored:
                content = item.get("content", "")
                score_map[content] = item["composite_score"]

            scored_entries: List[tuple] = []
            unscored_entries: List[str] = []

            for entry in entries:
                if entry in score_map:
                    scored_entries.append((score_map[entry], entry))
                else:
                    unscored_entries.append(entry)

            scored_entries.sort(key=lambda x: x[0], reverse=True)
            ranked = [entry for _, entry in scored_entries] + unscored_entries
            return self._select_within_budget(ranked, char_budget)

        except Exception as e:
            logger.warning("rank_and_select_entries failed: %s — falling back to flat order", e)
            return self._select_within_budget(entries, char_budget)

    @staticmethod
    def _select_within_budget(entries: List[str], char_budget: int) -> List[str]:
        """Select entries from the list that fit within the character budget."""
        if not entries or char_budget <= 0:
            return []

        delimiter = "\n§\n"
        selected: List[str] = []
        total = 0

        for entry in entries:
            needed = len(entry) + (len(delimiter) if selected else 0)
            if total + needed > char_budget:
                continue
            selected.append(entry)
            total += needed

        return selected

    @staticmethod
    def _default_context_query(target: str) -> str:
        """Generate a broad default query for ranking when no session context is available."""
        if target == "user":
            return "user preferences workflow habits communication style"
        return "environment tools project conventions workflow patterns"

    # -- Scoring and formatting internals -----------------------------------

    @staticmethod
    def _format_results(raw_results: Dict) -> List[Dict[str, Any]]:
        """Convert ChromaDB query results into a flat list of dicts."""
        results = []
        if not raw_results or not raw_results.get("ids"):
            return results

        ids = raw_results["ids"][0] if raw_results["ids"] else []
        docs = raw_results["documents"][0] if raw_results.get("documents") else []
        metas = raw_results["metadatas"][0] if raw_results.get("metadatas") else []
        dists = raw_results["distances"][0] if raw_results.get("distances") else []

        for i, doc_id in enumerate(ids):
            results.append({
                "id": doc_id,
                "content": docs[i] if i < len(docs) else "",
                "metadata": metas[i] if i < len(metas) else {},
                "distance": dists[i] if i < len(dists) else 1.0,
            })

        return results

    def _score_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compute composite scores: 0.5*similarity + 0.3*recency + 0.2*importance."""
        now = time.time()
        thirty_days = 30 * 24 * 3600

        scored = []
        for r in results:
            distance = r.get("distance", 1.0)
            meta = r.get("metadata", {})

            similarity = 1.0 / (1.0 + distance)

            stored_at = meta.get("stored_at", now)
            try:
                stored_at = float(stored_at)
            except (TypeError, ValueError):
                stored_at = now
            age = max(0, now - stored_at)
            recency = max(0.0, 1.0 - (age / thirty_days))

            importance = 0.5
            try:
                importance = float(meta.get("importance", 0.5))
            except (TypeError, ValueError):
                pass
            importance = max(0.0, min(1.0, importance))

            composite = (
                self._w_sim * similarity
                + self._w_rec * recency
                + self._w_imp * importance
            )

            entry = dict(r)
            entry["similarity"] = similarity
            entry["recency"] = recency
            entry["importance"] = importance
            entry["composite_score"] = composite
            scored.append(entry)

        return scored

    # -- Helpers ------------------------------------------------------------

    def _get_consolidator(self):
        """Get or create the MemoryConsolidator instance."""
        if self._consolidator is None:
            from plugins.memory.chromadb.consolidation import MemoryConsolidator
            self._consolidator = MemoryConsolidator()
        return self._consolidator


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register ChromaDB as a memory provider plugin."""
    ctx.register_memory_provider(ChromaDBMemoryProvider())
