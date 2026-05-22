"""Goal 1B observability helpers for ChromaDB memory.

G1B is intentionally local and append-only. It parses G1A boot synthesis
receipts, writes a small feedback ledger, and extracts correction markers
without importing ChromaDB, mutating vector stores, or touching MEMORY.md/USER.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FEEDBACK_SCHEMA_VERSION = 1
FEEDBACK_FILE = "memory_feedback.jsonl"
BOOT_RECEIPT_FILE = "boot_synthesis.jsonl"

ALLOWED_EVENT_TYPES = {
    "boot_selected",
    "boot_dropped",
    "correction_marker",
    "recall_needed",
    "recall_retrieved",
    "recall_used",
    "recall_skipped",
}

_CORRECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("you_know_this", re.compile(r"\byou\s+(?:already\s+)?know\s+this\b", re.I)),
    ("already_discussed", re.compile(r"\b(?:we\s+)?already\s+(?:discussed|covered|talked\s+about)\s+this\b", re.I)),
    ("why_asking", re.compile(r"\bwhy\s+are\s+you\s+asking\b", re.I)),
    ("dont_remember", re.compile(r"\b(?:do\s+not|don't|dont)\s+remember\s+(?:this|that)\b", re.I)),
    ("same_as_before", re.compile(r"\bsame\s+as\s+before\b", re.I)),
]


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _counter_dict(counter: Counter[str]) -> Dict[str, int]:
    return {k: counter[k] for k in sorted(counter)}


class JsonlRecords(list[dict[str, Any]]):
    """JSONL records plus file-level parse metadata."""

    def __init__(self, records: Iterable[dict[str, Any]] = (), *, malformed_count: int = 0, missing: bool = False):
        super().__init__(records)
        self.malformed_count = int(malformed_count or 0)
        self.missing = bool(missing)

    def tail(self, limit: int) -> "JsonlRecords":
        if limit <= 0:
            return JsonlRecords([], malformed_count=self.malformed_count, missing=self.missing)
        return JsonlRecords(self[-limit:], malformed_count=self.malformed_count, missing=self.missing)


def _read_jsonl(path: str | Path) -> JsonlRecords:
    p = Path(path).expanduser()
    if not p.exists():
        return JsonlRecords([], malformed_count=0, missing=True)
    records: list[dict[str, Any]] = []
    malformed = 0
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                malformed += 1
                continue
            if isinstance(obj, dict):
                record = dict(obj)
                record.setdefault("_missing", False)
                records.append(record)
            else:
                malformed += 1
    for record in records:
        record["_malformed_count"] = malformed
    return JsonlRecords(records, malformed_count=malformed, missing=False)


def read_boot_receipts(path: str | Path) -> JsonlRecords:
    return _read_jsonl(path)


def read_feedback_events(path: str | Path) -> JsonlRecords:
    return _read_jsonl(path)


def iter_boot_receipts(path: str | Path) -> JsonlRecords:
    return read_boot_receipts(path)


def iter_feedback_events(path: str | Path) -> JsonlRecords:
    return read_feedback_events(path)


def _extract_id(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        return str(item.get("id") or item.get("fact_id") or ""), str(item.get("reason") or item.get("drop_reason") or "unknown")
    return str(item or ""), "unknown"


def summarize_boot_receipts(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    malformed = int(getattr(records, "malformed_count", 0) or 0)
    has_missing_attr = hasattr(records, "missing")
    missing = bool(getattr(records, "missing", False)) if has_missing_attr else False
    rows = list(records)
    if not has_missing_attr:
        malformed = max((int(r.get("_malformed_count", 0) or 0) for r in rows), default=0)
        missing = bool(rows[0].get("_missing", False)) if rows else True
    models: Counter[str] = Counter()
    fallbacks: Counter[str] = Counter()
    selected: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    drop_reasons: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    durability: Counter[str] = Counter()
    hashes: Counter[str] = Counter()
    latencies: list[float] = []
    fallback_count = 0

    for r in rows:
        model = str(r.get("model") or "unknown")
        models[model] += 1
        if r.get("fallback_path_taken"):
            fallback_count += 1
            fallbacks[str(r.get("fallback_reason") or "unknown")] += 1
        if r.get("output_sha256"):
            hashes[str(r.get("output_sha256"))] += 1
        try:
            latencies.append(float(r.get("latency_ms", 0) or 0))
        except Exception:
            pass
        for fid in r.get("selected_ids") or []:
            if fid:
                selected[str(fid)] += 1
        for item in r.get("dropped_ids") or []:
            fid, reason = _extract_id(item)
            if fid:
                dropped[fid] += 1
                drop_reasons[reason or "unknown"] += 1
        for c in r.get("candidates") or []:
            if not isinstance(c, dict):
                continue
            meta = _candidate_metadata(c)
            sources[str(meta.get("source") or "unknown")] += 1
            durability[str(c.get("durability_label") or c.get("durability") or "unknown")] += 1

    latency_summary = {"count": len(latencies), "min": None, "max": None, "avg": None}
    if latencies:
        latency_summary = {
            "count": len(latencies),
            "min": int(min(latencies)) if min(latencies).is_integer() else min(latencies),
            "max": int(max(latencies)) if max(latencies).is_integer() else max(latencies),
            "avg": round(sum(latencies) / len(latencies), 3),
        }

    return {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "missing": missing,
        "receipt_count": len(rows),
        "malformed_count": malformed,
        "fallback_count": fallback_count,
        "fallback_rate": (fallback_count / len(rows)) if rows else 0.0,
        "fallback_reasons": _counter_dict(fallbacks),
        "models": _counter_dict(models),
        "latency_ms": latency_summary,
        "selected_ids": _counter_dict(selected),
        "dropped_ids": _counter_dict(dropped),
        "drop_reasons": _counter_dict(drop_reasons),
        "sources": _counter_dict(sources),
        "durability": _counter_dict(durability),
        "output_hashes": _counter_dict(hashes),
    }


def summarize_feedback_events(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    malformed = int(getattr(records, "malformed_count", 0) or 0)
    rows = list(records)
    if not hasattr(records, "malformed_count"):
        malformed = max((int(r.get("_malformed_count", 0) or 0) for r in rows), default=0)
    event_types: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    facts: Counter[str] = Counter()
    for r in rows:
        event_types[str(r.get("event_type") or "unknown")] += 1
        if r.get("fact_id"):
            facts[str(r.get("fact_id"))] += 1
        for label in r.get("labels") or []:
            labels[str(label)] += 1
    return {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "event_count": len(rows),
        "malformed_count": malformed,
        "event_types": _counter_dict(event_types),
        "labels": _counter_dict(labels),
        "fact_ids": _counter_dict(facts),
    }


def extract_correction_markers(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for label, pattern in _CORRECTION_PATTERNS:
        for match in pattern.finditer(text or ""):
            key = (label, match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "label": label,
                "start": match.start(),
                "end": match.end(),
                "span_sha256": _sha256(match.group(0).lower()),
            })
    found.sort(key=lambda m: (int(m["start"]), str(m["label"])))
    return found


def append_feedback_event(
    path: str | Path,
    *,
    event_type: str,
    session_id: str = "",
    platform: str = "cli",
    gateway_session_key: Optional[str] = None,
    fact_id: str = "",
    collection: str = "",
    source: str = "",
    target: str = "",
    labels: Optional[list[str]] = None,
    context: str = "",
    context_sha256: str = "",
    timestamp: str = "",
    **extra: Any,
) -> dict[str, Any]:
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"Unsupported G1B feedback event_type: {event_type}")
    record: dict[str, Any] = {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "timestamp": timestamp or _now_iso(),
        "session_id": session_id or "",
        "platform": platform or "cli",
        "gateway_session_key": gateway_session_key,
        "event_type": event_type,
        "fact_id": fact_id or "",
        "collection": collection or "",
        "source": source or "",
        "target": target or "",
        "labels": list(labels or []),
        "context_sha256": context_sha256 or _sha256(context or ""),
    }
    for k, v in extra.items():
        if k not in record and k != "context":
            record[k] = v
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return record


def feedback_path_for_home(hermes_home: str | Path) -> Path:
    home = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"
    return home / "logs" / FEEDBACK_FILE


def boot_receipt_path_for_home(hermes_home: str | Path) -> Path:
    home = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"
    return home / "logs" / BOOT_RECEIPT_FILE


def _candidate_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy/raw candidates and actual G1A receipt candidates."""
    raw = candidate.get("metadata") or {}
    source_meta = candidate.get("source_metadata") or {}
    target_meta = candidate.get("target_metadata") or {}
    meta: dict[str, Any] = {}
    for mapping in (raw, source_meta, target_meta):
        if isinstance(mapping, dict):
            meta.update({k: v for k, v in mapping.items() if v not in (None, "")})
    return meta


def _candidate_map(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in receipt.get("candidates") or []:
        if isinstance(c, dict) and c.get("id"):
            out[str(c.get("id"))] = c
    return out


def feedback_events_from_boot_receipt(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = _candidate_map(receipt)
    session_id = str(receipt.get("session_id") or "")
    platform = str(receipt.get("platform") or "cli")
    gateway_key = receipt.get("gateway_session_key")
    events: list[dict[str, Any]] = []
    for fid in receipt.get("selected_ids") or []:
        fid = str(fid or "")
        if not fid:
            continue
        cand = candidates.get(fid, {})
        meta = _candidate_metadata(cand)
        label = str(cand.get("durability_label") or cand.get("durability") or "")
        events.append({
            "event_type": "boot_selected",
            "session_id": session_id,
            "platform": platform,
            "gateway_session_key": gateway_key,
            "fact_id": fid,
            "collection": str(meta.get("collection") or meta.get("collection_name") or ""),
            "source": str(meta.get("source") or ""),
            "target": str(meta.get("target") or ""),
            "labels": [label] if label else [],
        })
    for item in receipt.get("dropped_ids") or []:
        fid, reason = _extract_id(item)
        if not fid:
            continue
        cand = candidates.get(fid, {})
        meta = _candidate_metadata(cand)
        label = str(cand.get("durability_label") or cand.get("durability") or "")
        labels = [x for x in [label, reason] if x]
        events.append({
            "event_type": "boot_dropped",
            "session_id": session_id,
            "platform": platform,
            "gateway_session_key": gateway_key,
            "fact_id": fid,
            "collection": str(meta.get("collection") or meta.get("collection_name") or ""),
            "source": str(meta.get("source") or ""),
            "target": str(meta.get("target") or ""),
            "labels": labels,
        })
    return events


def append_feedback_events_from_boot_receipt(hermes_home: str | Path, receipt: dict[str, Any]) -> int:
    path = feedback_path_for_home(hermes_home)
    count = 0
    for event in feedback_events_from_boot_receipt(receipt):
        append_feedback_event(path, context_sha256=str(receipt.get("output_sha256") or ""), **event)
        count += 1
    return count
