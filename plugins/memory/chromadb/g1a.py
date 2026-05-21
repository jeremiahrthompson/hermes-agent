"""Goal 1A boot synthesis helpers for the ChromaDB memory provider.

All helpers are read-only with respect to ChromaDB. Runtime side effects are
limited to appending the boot-synthesis JSONL receipt under $HERMES_HOME/logs.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import math
import re
import string
import time
import unicodedata
import urllib.error
import urllib.request
import socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

W_SIM = 0.35
W_REC = 0.25
W_SOURCE = 0.20
W_IMPORTANCE = 0.15
W_DURABILITY = 0.05
RECENCY_WINDOW_SECONDS = 30 * 24 * 3600
NEAR_DUPLICATE_THRESHOLD = 0.92
SYNTHESIS_MODEL = "qwen2.5:7b"
SYNTHESIS_TIMEOUT_SECONDS = 8.0

assert abs((W_SIM + W_REC + W_SOURCE + W_IMPORTANCE + W_DURABILITY) - 1.0) < 1e-9

SOURCE_QUALITY = {
    "builtin_mirror": 1.0,
    "memory": 1.0,
    "user": 1.0,
    "hand_authored": 1.0,
    "hand-authored": 1.0,
    "seed": 0.8,
    "pre_compress_extraction": 0.5,
    "session_turn": 0.1,
    "<missing>": 0.1,
}

DURABILITY_SCORE = {"durable": 1.0, "time-bound": 0.55, "ephemeral": 0.0}

REQUIRED_RECEIPT_FIELDS = [
    "timestamp",
    "session_id",
    "platform",
    "gateway_session_key",
    "query_strings",
    "collections_searched",
    "candidates",
    "selected_ids",
    "dropped_ids",
    "pre_dedup_count",
    "post_dedup_count",
    "model",
    "input_chars",
    "output_chars",
    "latency_ms",
    "fallback_path_taken",
    "fallback_reason",
    "output_sha256",
    "previous_block_sha256",
    "diff_summary",
]

_DROP_REASONS = {
    "duplicate", "over_budget", "low_score", "stale", "unsafe",
    "superseded", "ephemeral", "out_of_validity_window",
}

class BootSynthesisError(RuntimeError):
    """Base class for boot synthesis fallback-worthy errors."""

class ModelUnavailable(BootSynthesisError):
    """Ollama/qwen is unreachable."""

class ModelTimeout(ModelUnavailable):
    """Ollama/qwen exceeded the hard boot timeout."""

class EmptyOutput(ModelUnavailable):
    """The synthesis model returned no usable text."""

class UnsafeOutput(BootSynthesisError):
    """The synthesis model returned output unsafe for prompt insertion."""


def _float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


def source_quality_score(metadata: Optional[Dict[str, Any]]) -> float:
    meta = metadata or {}
    source = meta.get("source")
    if not isinstance(source, str) or not source.strip():
        source = "<missing>"
    source = source.strip().lower()
    if source in SOURCE_QUALITY:
        return SOURCE_QUALITY[source]
    # Hand-authored flat-file equivalents can appear as provenance/target in
    # sparse historical rows; honor them without rewriting Chroma metadata.
    provenance = str(meta.get("provenance") or meta.get("origin") or "").lower()
    target = str(meta.get("target") or "").lower()
    if provenance in {"memory.md", "user.md", "memory", "user", "flat_file", "flat-file"}:
        return 1.0
    if target in {"memory", "user"} and source in {"flat_file", "flat-file", "hand_authored", "hand-authored"}:
        return 1.0
    return SOURCE_QUALITY["session_turn"]


def _contains_any(text: str, patterns: Sequence[str]) -> bool:
    lower = text.lower()
    return any(p in lower for p in patterns)


def durability_label(content: str, metadata: Optional[Dict[str, Any]] = None, *, now: Optional[float] = None) -> str:
    """Classify a candidate as durable, time-bound, or ephemeral.

    Deterministic heuristics only; never persists labels back to Chroma.
    """
    meta = metadata or {}
    text = f"{content or ''} {json.dumps(meta, sort_keys=True, default=str)}".lower()
    sha_like = re.search(r"\b(?=[0-9a-f]{7,40}\b)[0-9a-f]*[a-f][0-9a-f]*\b", text)
    if re.search(r"\b(pr|pull request)\s*#?\d+\b", text) or sha_like:
        return "ephemeral"
    if _contains_any(text, [
        "phase ", "task progress", "todo", "done", "merged", "opened pr",
        "commit ", "branch ", "ci is", "status update", "temporary",
        "session ", "run completed", "artifact ", "log file",
    ]):
        return "ephemeral"
    if _contains_any(text, [
        "application", "applied", "interview", "job", "deal", "in flight",
        "in-flight", "opportunity", "deadline", "expires", "valid_until",
        "current project", "active", "campaign", "outreach",
    ]):
        return "time-bound"
    if _contains_any(text, [
        "prefers", "preference", "user profile", "identity", "legal", "durable",
        "long-term", "remember", "stable", "convention", "profile", "faith",
        "role", "timezone", "environment", "uses ", "expects", "correction",
    ]):
        return "durable"
    target = str(meta.get("target") or "").lower()
    source = str(meta.get("source") or "").lower()
    if target == "user" or source in {"builtin_mirror", "seed"}:
        return "durable"
    return "durable"


def durability_score(label: str) -> float:
    return DURABILITY_SCORE.get(label, 0.0)


def score_result(result: Dict[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
    if now is None:
        now = time.time()
    meta = result.get("metadata") or {}
    distance = max(0.0, _float(result.get("distance", 1.0), 1.0))
    similarity = 1.0 / (1.0 + distance)
    stored_at = _float(meta.get("stored_at", now), now)
    age = max(0.0, now - stored_at)
    recency = max(0.0, 1.0 - (age / RECENCY_WINDOW_SECONDS))
    importance = max(0.0, min(1.0, _float(meta.get("importance", 0.5), 0.5)))
    src = source_quality_score(meta)
    label = durability_label(str(result.get("content") or ""), meta, now=now)
    dur = durability_score(label)
    composite = (W_SIM * similarity) + (W_REC * recency) + (W_SOURCE * src) + (W_IMPORTANCE * importance) + (W_DURABILITY * dur)
    out = dict(result)
    out.update({
        "similarity": similarity,
        "recency": recency,
        "source_quality": src,
        "importance": importance,
        "durability_label": label,
        "durability": dur,
        "composite_score": composite,
    })
    return out


def score_results(results: Iterable[Dict[str, Any]], *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    if now is None:
        now = time.time()
    return [score_result(r, now=now) for r in (results or [])]


def _parse_time(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
        from datetime import datetime
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def filter_candidates(candidates: Iterable[Dict[str, Any]], *, now: Optional[float] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if now is None:
        now = time.time()
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for raw in candidates or []:
        scored = score_result(raw, now=now) if "composite_score" not in raw else dict(raw)
        meta = scored.get("metadata") or {}
        label = scored.get("durability_label") or durability_label(str(scored.get("content") or ""), meta, now=now)
        scored["durability_label"] = label
        if label == "ephemeral":
            dropped.append(_drop(scored, "ephemeral"))
            continue
        if label == "time-bound":
            valid_until = _parse_time(meta.get("valid_until") or meta.get("expires_at") or meta.get("valid_through"))
            valid_from = _parse_time(meta.get("valid_from") or meta.get("starts_at"))
            if valid_until is None:
                dropped.append(_drop(scored, "out_of_validity_window"))
                continue
            if valid_until < now:
                dropped.append(_drop(scored, "out_of_validity_window"))
                continue
            if valid_from is not None and valid_from > now:
                dropped.append(_drop(scored, "out_of_validity_window"))
                continue
        kept.append(scored)
    return kept, dropped


def normalize_content(text: str) -> str:
    norm = unicodedata.normalize("NFKC", text or "").lower().strip()
    norm = re.sub(r"\s+", " ", norm)
    # Remove punctuation-only volatility while preserving word boundaries.
    punct = re.escape(string.punctuation.replace("-", ""))
    norm = re.sub(f"[{punct}]", "", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_content(text).encode("utf-8")).hexdigest()


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _representative_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
    meta = row.get("metadata") or {}
    return (
        float(row.get("source_quality", source_quality_score(meta))),
        float(row.get("importance", _float(meta.get("importance", 0.5), 0.5))),
        float(row.get("durability", durability_score(row.get("durability_label", "durable")))),
        float(row.get("recency", 0.0)),
        float(row.get("similarity", 0.0)),
    )


def _drop(row: Dict[str, Any], reason: str) -> Dict[str, Any]:
    assert reason in _DROP_REASONS
    return {
        "id": row.get("id", ""),
        "reason": reason,
        "source": (row.get("metadata") or {}).get("source", "<missing>"),
        "target": (row.get("metadata") or {}).get("target"),
        "durability_label": row.get("durability_label"),
        "composite_score": row.get("composite_score"),
    }


def deduplicate_candidates(
    candidates: Iterable[Dict[str, Any]],
    *,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    scored = [score_result(c) if "composite_score" not in c else dict(c) for c in (candidates or [])]
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    by_hash: Dict[str, int] = {}
    for row in scored:
        h = content_hash(str(row.get("content") or ""))
        row["normalized_content_sha256"] = h
        if h in by_hash:
            idx = by_hash[h]
            if _representative_key(row) > _representative_key(kept[idx]):
                dropped.append(_drop(kept[idx], "superseded"))
                kept[idx] = row
            else:
                dropped.append(_drop(row, "duplicate"))
            continue
        by_hash[h] = len(kept)
        kept.append(row)

    if embed_fn and len(kept) > 1:
        try:
            embeddings = embed_fn([str(r.get("content") or "") for r in kept])
        except Exception as e:
            logger.debug("G1A embedding dedup skipped: %s", e)
            return kept, dropped
        final: List[Dict[str, Any]] = []
        final_embs: List[List[float]] = []
        for row, emb in zip(kept, embeddings):
            dup_idx = None
            for i, existing_emb in enumerate(final_embs):
                if cosine_similarity(emb, existing_emb) >= threshold:
                    dup_idx = i
                    break
            if dup_idx is None:
                final.append(row)
                final_embs.append(emb)
                continue
            if _representative_key(row) > _representative_key(final[dup_idx]):
                dropped.append(_drop(final[dup_idx], "superseded"))
                final[dup_idx] = row
                final_embs[dup_idx] = emb
            else:
                dropped.append(_drop(row, "duplicate"))
        kept = final
    kept.sort(key=lambda r: float(r.get("composite_score", 0.0)), reverse=True)
    return kept, dropped


def candidate_receipt(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("metadata") or {}
    return {
        "id": row.get("id", ""),
        "raw_score": row.get("composite_score"),
        "composite_score": row.get("composite_score"),
        "similarity": row.get("similarity"),
        "recency": row.get("recency"),
        "stored_at": meta.get("stored_at"),
        "source_quality": row.get("source_quality"),
        "importance": row.get("importance"),
        "durability_label": row.get("durability_label"),
        "source_metadata": {
            "source": meta.get("source", "<missing>"),
            "target": meta.get("target"),
            "stored_at": meta.get("stored_at"),
            "valid_until": meta.get("valid_until") or meta.get("expires_at"),
        },
        "target_metadata": {
            "target": meta.get("target"),
            "source": meta.get("source", "<missing>"),
        },
    }


def _unsafe_output(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in ["<system", "</system", "ignore previous instructions", "jailbreak"])


def synthesize_with_ollama(
    *,
    prompt: str,
    model: str = SYNTHESIS_MODEL,
    timeout: float = SYNTHESIS_TIMEOUT_SECONDS,
    host: str = "http://127.0.0.1:11434",
) -> str:
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 220},
    }).encode("utf-8")
    req = urllib.request.Request(
        host.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as e:
        raise ModelTimeout(str(e)) from e
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), socket.timeout):
            raise ModelTimeout(str(e)) from e
        raise ModelUnavailable(str(e)) from e
    except (OSError, json.JSONDecodeError) as e:
        raise ModelUnavailable(str(e)) from e
    text = str(data.get("response") or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    if not text:
        raise EmptyOutput("empty synthesis response")
    if _unsafe_output(text):
        raise UnsafeOutput("unsafe synthesis response")
    return text


def select_synthesis_candidates(candidates: Sequence[Dict[str, Any]], *, limit: int = 8) -> List[Dict[str, Any]]:
    """Choose the bounded fact set that is actually sent to qwen."""
    ordered = list(candidates or [])
    durable_user = [c for c in ordered if (c.get("metadata") or {}).get("target") == "user" and c.get("durability_label") == "durable"]
    prompt_candidates: List[Dict[str, Any]] = []
    if durable_user:
        prompt_candidates.append(durable_user[0])
    for c in ordered:
        if c not in prompt_candidates:
            prompt_candidates.append(c)
        if len(prompt_candidates) >= limit:
            break
    return prompt_candidates


def build_synthesis_prompt(candidates: Sequence[Dict[str, Any]], *, max_chars: int = 2200) -> str:
    facts = []
    # Keep boot latency bounded. Favor top-ranked facts, but force at least
    # one durable USER fact through when present so the boot block preserves
    # the contract's profile reachability guarantee.
    prompt_candidates = select_synthesis_candidates(candidates, limit=8)
    for c in prompt_candidates:
        facts.append({
            "id": c.get("id"),
            "target": (c.get("metadata") or {}).get("target"),
            "durability": c.get("durability_label"),
            "score": round(float(c.get("composite_score", 0.0)), 4),
            "content": str(c.get("content") or "")[:260],
        })
    return (
        "Synthesize a concise Hermes boot memory profile from these Chroma facts. "
        "Return only a <memory-profile source=\"chromadb\" degraded=\"false\"> block with at most 8 short bullet lines of the form - > \"fact\". "
        f"Keep total output <= {max_chars} characters. Prefer durable USER facts. "
        "Do not include PR numbers, commit SHAs, task progress, or transient state.\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
    )


def enforce_budget(text: str, max_chars: int) -> str:
    if len(text or "") <= max_chars:
        return text
    return (text or "")[: max(0, max_chars - 1)].rstrip() + "…"


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def previous_receipt_info(hermes_home: str) -> Tuple[Optional[str], Optional[str]]:
    path = Path(hermes_home).expanduser() / "logs" / "boot_synthesis.jsonl"
    if not path.exists():
        return None, None
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return None, None
        prev = json.loads(lines[-1])
        return prev.get("output_sha256"), prev.get("output_text")
    except Exception:
        return None, None


def diff_summary(previous_preview: Optional[str], current: str) -> Optional[str]:
    if previous_preview is None:
        return None
    diff = list(difflib.unified_diff(previous_preview.splitlines(), (current or "").splitlines(), lineterm=""))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return f"lines_added={added}, lines_removed={removed}"


def _strip_vectors(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_vectors(v) for k, v in obj.items() if k not in {"embedding", "embeddings", "vectors"}}
    if isinstance(obj, list):
        # Drop vector-looking numeric arrays entirely from receipts.
        if obj and all(isinstance(x, (int, float)) for x in obj):
            return "[redacted-vector]"
        return [_strip_vectors(v) for v in obj]
    return obj


class BootSynthesisReceiptWriter:
    def __init__(self, hermes_home: str):
        self.hermes_home = str(Path(hermes_home).expanduser()) if hermes_home else str(Path.home() / ".hermes")
        self.path = Path(self.hermes_home) / "logs" / "boot_synthesis.jsonl"
        self._guards: set[str] = set()

    def base_receipt(self, *, session_id: str, platform: str, gateway_session_key: Optional[str]) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "session_id": session_id or "",
            "platform": platform or "cli",
            "gateway_session_key": gateway_session_key,
        }

    def append_once(self, receipt: Dict[str, Any], *, guard_key: str) -> None:
        if guard_key in self._guards:
            return
        self._guards.add(guard_key)
        safe = _strip_vectors(dict(receipt or {}))
        safe.setdefault("fallback_reason", None)
        safe.setdefault("output_text_preview", None)
        missing = [field for field in REQUIRED_RECEIPT_FIELDS if field not in safe]
        if missing:
            logger.warning("G1A receipt missing required fields: %s", missing)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(safe, sort_keys=True, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning("Failed to append G1A boot synthesis receipt: %s", e)
