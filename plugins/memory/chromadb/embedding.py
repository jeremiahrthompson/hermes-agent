"""ChromaDB Memory Plugin — Embedding Function.

ForgeEmbeddingFunction calls the forge embedding service at http://100.113.1.2:8006.
Falls back to OpenRouter embeddings (same model family only), then fails closed.

Ported from tools/vector_memory.py ForgeEmbeddingFunction.
"""

from __future__ import annotations

import json as _json
import logging
import os
import urllib.request
from typing import List, Optional

logger = logging.getLogger(__name__)

# Map Forge model names to OpenRouter model identifiers (same-family only)
_FORGE_TO_OPENROUTER_MODEL = {
    "Qwen/Qwen3-Embedding-0.6B": "qwen/qwen3-embedding-0.6b",
    "qwen/qwen3-embedding-0.6b": "qwen/qwen3-embedding-0.6b",
}


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


class OpenRouterEmbeddingFunction:
    """ChromaDB-compatible embedding function via OpenRouter embeddings API.

    Uses the same model family as forge (e.g. qwen/qwen3-embedding-0.6b)
    to produce dimension-compatible embeddings (1024-dim).
    """

    @staticmethod
    def name() -> str:
        return "openrouter_embedding"

    def __init__(self, model: str, api_base: str = "https://openrouter.ai/api/v1"):
        self._api_base = api_base.rstrip("/")
        self._model = model
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is not set")
        self._api_key = api_key
        # Verify connectivity with a tiny probe embedding
        try:
            self._embed_batch(["ping"])
            logger.info(
                "OpenRouterEmbeddingFunction: connected via %s (model=%s)",
                self._api_base, self._model,
            )
        except Exception as e:
            raise RuntimeError(
                f"OpenRouter embedding probe failed for model {self._model}: {e}"
            ) from e

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Call the OpenRouter /embeddings endpoint (OpenAI-compatible)."""
        payload = _json.dumps({"model": self._model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self._api_base}/embeddings",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read())
        # OpenAI-format response: {"data": [{"embedding": [...], "index": 0}, ...]}
        data = result.get("data", [])
        # Sort by index to guarantee order
        data.sort(key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in data]

    def __call__(self, input: List[str]) -> List[List[float]]:
        """Embed a list of texts via OpenRouter."""
        try:
            return self._embed_batch(input)
        except Exception as e:
            logger.error("OpenRouterEmbeddingFunction failed: %s", e)
            raise


def get_embedding_function(
    embedding_service_url: str,
    embedding_model: str,
    *,
    fallback_enabled: bool = True,
    fallback_url: str = "https://openrouter.ai/api/v1",
) -> Optional[object]:
    """Get the best available embedding function with safe fallback chain.

    Order: Forge service > OpenRouter (same model only) > None (fail closed).

    Returns None ONLY if both providers are unavailable. Callers MUST treat
    None as "no safe embedding available" and refuse to embed rather than
    falling through to ChromaDB default/auto-embedding, which would produce
    dimension-mismatched vectors (384 vs 1024).
    """
    # Try Forge embedding service first
    if embedding_service_url:
        try:
            return ForgeEmbeddingFunction(embedding_service_url)
        except Exception as e:
            logger.warning(
                "Forge embedding service unavailable (%s), trying fallback", e
            )

    # Try OpenRouter fallback — same model family only
    if fallback_enabled:
        or_model = _FORGE_TO_OPENROUTER_MODEL.get(embedding_model)
        if or_model and os.environ.get("OPENROUTER_API_KEY"):
            try:
                return OpenRouterEmbeddingFunction(or_model, api_base=fallback_url)
            except Exception as e:
                logger.warning(
                    "OpenRouter embedding fallback unavailable (%s)", e
                )
        elif not or_model:
            logger.warning(
                "No OpenRouter model mapping for '%s' — skipping fallback "
                "(only same-model-family fallback is safe for existing 1024-dim collections)",
                embedding_model,
            )
        elif not os.environ.get("OPENROUTER_API_KEY"):
            logger.warning(
                "OPENROUTER_API_KEY not set — cannot use OpenRouter embedding fallback"
            )

    # Fail closed: do NOT silently return a FastEmbed or ChromaDB default EF.
    # Returning None signals callers to refuse embedding operations rather
    # than risking a 384-vs-1024 dimension mismatch on existing collections.
    logger.error(
        "No embedding provider available (forge and OpenRouter both failed). "
        "Embedding operations will be refused to protect existing 1024-dim collections."
    )
    return None
