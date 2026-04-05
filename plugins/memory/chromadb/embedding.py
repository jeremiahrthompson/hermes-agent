"""ChromaDB Memory Plugin — Embedding Function.

ForgeEmbeddingFunction calls the forge embedding service at http://100.113.1.2:8006.
Falls back to FastEmbed, then to ChromaDB's default embeddings.

Ported from tools/vector_memory.py ForgeEmbeddingFunction.
"""

from __future__ import annotations

import json as _json
import logging
import urllib.request
from typing import List, Optional

logger = logging.getLogger(__name__)


class ForgeEmbeddingFunction:
    """ChromaDB-compatible embedding function that calls forge's embedding service."""

    @staticmethod
    def name() -> str:
        return "forge_embedding"

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        # Verify service is reachable on init
        req = urllib.request.Request(f"{self._base_url}/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            info = _json.loads(resp.read())
            logger.info(
                "ForgeEmbeddingFunction: connected to %s (%s, %sd)",
                self._base_url, info.get("model", "?"), info.get("dimensions", "?")
            )

    def __call__(self, input: List[str]) -> List[List[float]]:
        """Embed a list of texts via forge's embedding service."""
        data = _json.dumps({"texts": input}).encode()
        req = urllib.request.Request(
            f"{self._base_url}/embed",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read())
                return result["embeddings"]
        except Exception as e:
            logger.error("ForgeEmbeddingFunction failed: %s", e)
            raise


def get_embedding_function(embedding_service_url: str, embedding_model: str) -> Optional[object]:
    """Get the best available embedding function with fallback chain.

    Order: Forge service > FastEmbed > ChromaDB default (None).
    """
    # Try Forge embedding service first
    if embedding_service_url:
        try:
            return ForgeEmbeddingFunction(embedding_service_url)
        except Exception as e:
            logger.warning(
                "Forge embedding service unavailable (%s), trying FastEmbed fallback", e
            )

    # Fallback to local FastEmbed — BUT only if collections haven't been
    # created with different dimensions.  FastEmbed models produce 384-dim
    # while Forge produces 1024-dim.  A dimension mismatch crashes ChromaDB.
    # Rather than silently switching to an incompatible model, return None
    # so ChromaDB uses whatever EF is already persisted in the collection.
    logger.warning(
        "Forge embedding service unavailable and FastEmbed fallback "
        "risks dimension mismatch (384 vs 1024). "
        "Returning None — ChromaDB will use its persisted embedding config."
    )
    return None
