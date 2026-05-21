"""ChromaDB Memory Plugin — Prompt Profile Cache.

Hermes-home-scoped cache for the generated profile block.

Design constraints (see plans/2026-05-21-chromadb-generated-profile-memory-plan.md):

* Cache files live under ``$HERMES_HOME/cache/`` only — never in the repo or
  the current working directory.
* Files are written with mode ``0600`` via ``os.open(..., 0o600)`` followed
  by a defensive ``os.chmod`` so the umask cannot widen the permissions.
* Cache payloads MUST NOT contain raw embedding vectors.  The writer
  defensively strips any ``embeddings``/``vectors``/``embedding`` keys at
  every depth — a misbehaving caller cannot leak a vector through this
  layer.
* Corrupted cache files fail closed: ``read_cache`` returns ``None`` rather
  than raising, so the provider falls through to legacy / unavailable marker.
* Stale cache (older than ``ttl_seconds``) is only returned when the caller
  passes ``allow_stale=True``; in that case the result is marked
  ``degraded=True`` so the calling provider can keep legacy memory additive.
* Mutation invalidation: ``invalidate_target`` removes every cached
  artifact for a profile/target pair so the next session-start prompt
  build picks up fresh content.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR_NAME = "cache"
_CACHE_VERSION = 1
_RAW_VECTOR_KEYS = {"embeddings", "vectors", "embedding"}


def _safe_token(s: str, fallback: str) -> str:
    """Sanitize a token used in a cache filename — alnum, dash, underscore only."""
    if not s:
        return fallback
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in s)
    return out or fallback


def _cache_dir(hermes_home: str) -> Path:
    d = Path(hermes_home) / _CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _warn_if_inside_git_tree(path: Path) -> None:
    """Plan rule: emit a high-severity warning if $HERMES_HOME resolves
    inside a git working tree.  We don't refuse to write — that would be
    worse than a warning — but we make sure operators notice.
    """
    try:
        for parent in [path, *path.parents]:
            if (parent / ".git").exists():
                logger.warning(
                    "CHROMADB_CACHE_INSIDE_GIT_TREE: cache path %s sits inside a "
                    "git working tree (%s). Cache files are mode 0600 but should "
                    "not be committed.",
                    str(path), str(parent),
                )
                return
    except Exception:
        # Best-effort warning — never raise from cache path resolution.
        pass


def cache_path_for(hermes_home: str, *, profile: str, cache_key: str,
                   target: str = "profile") -> str:
    """Return the absolute path for a given (profile, target, cache_key)."""
    safe_profile = _safe_token(profile, "default")
    safe_key = _safe_token(cache_key, "nokey")
    safe_target = _safe_token(target, "profile")
    cache_dir = _cache_dir(hermes_home)
    return str(cache_dir / f"chromadb-{safe_profile}-{safe_target}-{safe_key}.json")


def _strip_raw_vectors(obj: Any) -> Any:
    """Defensively remove any embedding/vector arrays at any depth.

    The cache contract is "no raw vectors".  We enforce it here so a
    misbehaving caller (or future regression) cannot smuggle a vector into
    the cache file.
    """
    if isinstance(obj, dict):
        return {
            k: _strip_raw_vectors(v)
            for k, v in obj.items()
            if k not in _RAW_VECTOR_KEYS
        }
    if isinstance(obj, list):
        return [_strip_raw_vectors(v) for v in obj]
    return obj


def write_cache(
    hermes_home: str,
    *,
    profile: str,
    cache_key: str,
    payload: Dict[str, Any],
    target: str = "profile",
) -> str:
    """Atomically write a cache file with mode 0600.  Returns the path."""
    path = cache_path_for(hermes_home, profile=profile, cache_key=cache_key, target=target)
    _warn_if_inside_git_tree(Path(path))

    cleaned_payload = _strip_raw_vectors(payload or {})
    generated_at = cleaned_payload.get("generated_at")
    if not isinstance(generated_at, (int, float)):
        generated_at = time.time()

    envelope = {
        "version": _CACHE_VERSION,
        "target": target,
        "profile": profile,
        "cache_key": cache_key,
        "generated_at": float(generated_at),
        "payload": cleaned_payload,
    }
    data = json.dumps(envelope, sort_keys=True, ensure_ascii=True).encode("utf-8")

    # mode 0600 is mandatory; use O_CREAT|O_WRONLY|O_TRUNC + chmod to
    # survive the process umask (which may widen perms otherwise).
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning("Failed to set 0600 on %s: %s", path, e)
    return path


def _find_matching_files(hermes_home: str, *, profile: str, cache_key: str) -> List[Path]:
    cache_dir = Path(hermes_home) / _CACHE_DIR_NAME
    if not cache_dir.exists():
        return []
    safe_profile = _safe_token(profile, "default")
    safe_key = _safe_token(cache_key, "nokey")
    pattern = f"chromadb-{safe_profile}-*-{safe_key}.json"
    matches = list(cache_dir.glob(pattern))
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches


def read_cache(
    hermes_home: str,
    *,
    profile: str,
    cache_key: str,
    ttl_seconds: int,
    allow_stale: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return ``{payload, degraded, generated_at}`` or ``None``.

    * Returns ``None`` when no file exists or the file is corrupt.
    * Returns ``degraded=False`` when within ``ttl_seconds``.
    * Returns ``degraded=True`` when stale and ``allow_stale=True``.
    * Returns ``None`` when stale and ``allow_stale=False``.
    """
    matches = _find_matching_files(hermes_home, profile=profile, cache_key=cache_key)
    if not matches:
        return None
    path = matches[0]
    try:
        text = path.read_text(encoding="utf-8")
        envelope = json.loads(text)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("CHROMADB_CACHE_CORRUPT: %s (%s) — failing closed", path, e)
        return None

    if not isinstance(envelope, dict):
        logger.warning("CHROMADB_CACHE_CORRUPT: %s (non-dict envelope) — failing closed", path)
        return None

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return None

    generated_at = envelope.get("generated_at") or payload.get("generated_at") or 0.0
    try:
        generated_at = float(generated_at)
    except (TypeError, ValueError):
        generated_at = 0.0

    age = time.time() - generated_at
    if age <= max(0, int(ttl_seconds)):
        return {"payload": payload, "degraded": False, "generated_at": generated_at}
    if allow_stale:
        return {"payload": payload, "degraded": True, "generated_at": generated_at}
    return None


def invalidate_target(hermes_home: str, *, profile: str, target: str) -> int:
    """Remove all cached files for a given ``(profile, target)`` pair.

    Returns the number of files deleted.  Called by the provider on
    ``on_memory_write`` so the next session prompt build sees fresh
    content.  Best-effort: a missing cache directory is not an error.
    """
    cache_dir = Path(hermes_home) / _CACHE_DIR_NAME
    if not cache_dir.exists():
        return 0
    safe_profile = _safe_token(profile, "default")
    safe_target = _safe_token(target, "profile")
    pattern = f"chromadb-{safe_profile}-{safe_target}-*.json"
    deleted = 0
    for p in cache_dir.glob(pattern):
        try:
            p.unlink()
            deleted += 1
        except OSError as e:
            logger.debug("Failed to invalidate %s: %s", p, e)
    return deleted
