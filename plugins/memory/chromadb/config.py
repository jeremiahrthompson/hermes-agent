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
        }
