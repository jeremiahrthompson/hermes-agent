"""ChromaDB Memory Plugin — Configuration.

Loads from $HERMES_HOME/chromadb.json (profile-scoped).
Falls back to environment variables and sensible defaults.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Phase 1 hard defaults for the generated profile path. These are
# intentionally conservative — keys missing from chromadb.json must use
# these values without raising.  The core memory.generated_prompt
# config block decides whether generation runs at all.
_GP_DEFAULT_USER_QUERY = (
    "stable user preferences identity roles family constraints durable corrections"
)
_GP_DEFAULT_MEMORY_QUERY = (
    "stable operating conventions infrastructure project preferences durable lessons"
)


@dataclass
class GeneratedProfileConfig:
    """Provider-local tuning for the generated profile block.

    Enabled by default because core defaults use the safe
    ``provider_with_legacy_fallback`` mode: legacy MEMORY/USER markdown remains
    injected unless the provider returns a non-degraded generated profile.
    These knobs only refine retrieval/render behavior.
    """

    enabled: bool = True
    user_query: str = _GP_DEFAULT_USER_QUERY
    memory_query: str = _GP_DEFAULT_MEMORY_QUERY
    max_user_facts: int = 20
    max_memory_facts: int = 30
    min_confidence: float = 0.0
    include_team_knowledge: bool = False
    # Char budgets default to current legacy USER.md / MEMORY.md limits so
    # the byte-compat gate in Phase 2 has a stable reference.
    max_user_chars: int = 1375
    max_memory_chars: int = 2200
    cache_ttl_seconds: int = 86400
    fallback_to_cache: bool = True
    config_version: str = "v1"

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "GeneratedProfileConfig":
        if not data or not isinstance(data, dict):
            return cls()
        try:
            return cls(
                enabled=bool(data.get("enabled", True)),
                user_query=str(data.get("user_query", _GP_DEFAULT_USER_QUERY)),
                memory_query=str(data.get("memory_query", _GP_DEFAULT_MEMORY_QUERY)),
                max_user_facts=int(data.get("max_user_facts", 20)),
                max_memory_facts=int(data.get("max_memory_facts", 30)),
                min_confidence=float(data.get("min_confidence", 0.0)),
                include_team_knowledge=bool(data.get("include_team_knowledge", False)),
                max_user_chars=int(data.get("max_user_chars", 1375)),
                max_memory_chars=int(data.get("max_memory_chars", 2200)),
                cache_ttl_seconds=int(data.get("cache_ttl_seconds", 86400)),
                fallback_to_cache=bool(data.get("fallback_to_cache", True)),
                config_version=str(data.get("config_version", "v1")),
            )
        except (TypeError, ValueError) as e:
            logger.debug("Invalid generated_profile keys, using defaults: %s", e)
            return cls()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "user_query": self.user_query,
            "memory_query": self.memory_query,
            "max_user_facts": self.max_user_facts,
            "max_memory_facts": self.max_memory_facts,
            "min_confidence": self.min_confidence,
            "include_team_knowledge": self.include_team_knowledge,
            "max_user_chars": self.max_user_chars,
            "max_memory_chars": self.max_memory_chars,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "fallback_to_cache": self.fallback_to_cache,
            "config_version": self.config_version,
        }


@dataclass
class ChromaDBConfig:
    """Configuration for the ChromaDB vector memory plugin."""

    enabled: bool = True
    chromadb_host: str = "100.107.68.104"
    chromadb_port: int = 8000
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_service_url: str = "http://100.113.1.2:8006"
    collections: Dict[str, str] = field(default_factory=lambda: {
        "memories": "agent_memories",
        "sessions": "session_history",
        "team_knowledge": "team_knowledge",
        "team_ops": "team_ops",
        "agent_rilo": "agent_rilo",
        "agent_caddie": "agent_caddie",
        "agent_scout": "agent_scout",
    })
    # Composite scoring weights
    similarity_weight: float = 0.5
    recency_weight: float = 0.3
    importance_weight: float = 0.2
    # Embedding fallback (OpenRouter, same-model only)
    embedding_fallback_enabled: bool = True
    embedding_fallback_url: str = "https://openrouter.ai/api/v1"
    # Default char budget for memory injection
    default_char_budget: int = 2200
    # Agent identity (set during initialize)
    agent_name: str = "rilo"
    # Provider-local tuning for the generated profile block (Phase 1 / Lane B)
    generated_profile: GeneratedProfileConfig = field(default_factory=GeneratedProfileConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChromaDBConfig":
        """Create config from a dictionary (e.g. parsed JSON)."""
        if not data:
            return cls()
        default_collections = {
            "memories": "agent_memories",
            "sessions": "session_history",
            "team_knowledge": "team_knowledge",
            "team_ops": "team_ops",
            "agent_rilo": "agent_rilo",
            "agent_caddie": "agent_caddie",
            "agent_scout": "agent_scout",
        }
        collections = data.get("collections")
        if not isinstance(collections, dict):
            collections = default_collections
        return cls(
            enabled=bool(data.get("enabled", True)),
            chromadb_host=str(data.get("chromadb_host", os.environ.get("CHROMADB_HOST", "100.107.68.104"))),
            chromadb_port=int(data.get("chromadb_port", os.environ.get("CHROMADB_PORT", 8000))),
            embedding_model=str(data.get("embedding_model", "Qwen/Qwen3-Embedding-0.6B")),
            embedding_service_url=str(data.get("embedding_service_url",
                                                os.environ.get("EMBEDDING_SERVICE_URL", "http://100.113.1.2:8006"))),
            collections=collections,
            embedding_fallback_enabled=bool(data.get("embedding_fallback_enabled", True)),
            embedding_fallback_url=str(data.get("embedding_fallback_url", "https://openrouter.ai/api/v1")),
            similarity_weight=float(data.get("similarity_weight", 0.5)),
            recency_weight=float(data.get("recency_weight", 0.3)),
            importance_weight=float(data.get("importance_weight", 0.2)),
            default_char_budget=int(data.get("default_char_budget", 2200)),
            agent_name=str(data.get("agent_name", "rilo")),
            generated_profile=GeneratedProfileConfig.from_dict(data.get("generated_profile")),
        )

    @classmethod
    def from_json_file(cls, hermes_home: str) -> "ChromaDBConfig":
        """Load from $HERMES_HOME/chromadb.json."""
        try:
            config_path = Path(hermes_home) / "chromadb.json"
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                return cls.from_dict(data)
        except Exception as e:
            logger.debug("Failed to load chromadb.json: %s", e)

        # Fallback: try environment variables
        return cls.from_dict({})

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "enabled": self.enabled,
            "chromadb_host": self.chromadb_host,
            "chromadb_port": self.chromadb_port,
            "embedding_model": self.embedding_model,
            "embedding_service_url": self.embedding_service_url,
            "collections": self.collections,
            "embedding_fallback_enabled": self.embedding_fallback_enabled,
            "embedding_fallback_url": self.embedding_fallback_url,
            "similarity_weight": self.similarity_weight,
            "recency_weight": self.recency_weight,
            "importance_weight": self.importance_weight,
            "default_char_budget": self.default_char_budget,
            "agent_name": self.agent_name,
            "generated_profile": self.generated_profile.to_dict(),
        }
