#!/usr/bin/env python3
"""G1A manifest smoke test.

Read-only against Sentinel ChromaDB and Forge embedding service. Local side effect:
append one boot synthesis receipt under a temporary HERMES_HOME passed by caller
or the real Hermes home when used as an operator smoke.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from plugins.memory.chromadb import ChromaDBMemoryProvider  # noqa: E402
from plugins.memory.chromadb.config import ChromaDBConfig  # noqa: E402


def sha(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    hermes_home = Path(os.environ.get("G1A_SMOKE_HOME") or "/Users/jeremiah/.hermes")
    before = {name: sha(hermes_home / name) for name in ("MEMORY.md", "USER.md")}

    provider = ChromaDBMemoryProvider()
    config = ChromaDBConfig.from_json_file(str(hermes_home))
    provider._config = config
    provider._available = True
    provider._cron_skipped = False
    provider._hermes_home = str(hermes_home)
    provider._agent_name = "rilo"
    provider._session_id = f"g1a-smoke-{int(time.time())}"
    provider._platform = "cli"
    provider._gateway_session_key = None
    provider._agent_context = "primary"
    provider._prompt_source = "provider_with_legacy_fallback"
    provider._generated_profile_enabled = True
    provider._boot_synthesis_enabled = True
    provider._team_context = ""
    try:
        import chromadb
        from plugins.memory.chromadb.embedding import get_embedding_function
        client = chromadb.HttpClient(host=config.chromadb_host, port=config.chromadb_port)
        provider._client = client
        provider._ef = get_embedding_function(
            config.embedding_service_url,
            config.embedding_model,
            fallback_enabled=config.embedding_fallback_enabled,
            fallback_url=config.embedding_fallback_url,
        )
        # Read-only open: do not call get_or_create_collection in this smoke.
        provider._collections = {"memories": client.get_collection(config.collections["memories"])}
    except Exception as e:
        print(json.dumps({"ok": False, "reason": "chromadb_unavailable", "error": str(e)}, indent=2))
        return 2

    memories = provider._collections.get("memories")
    count = memories.count() if memories is not None and hasattr(memories, "count") else None
    block = provider._build_generated_profile_block()
    after = {name: sha(hermes_home / name) for name in ("MEMORY.md", "USER.md")}
    receipt_path = hermes_home / "logs" / "boot_synthesis.jsonl"
    receipt = {}
    if receipt_path.exists():
        lines = [l for l in receipt_path.read_text().splitlines() if l.strip()]
        if lines:
            receipt = json.loads(lines[-1])

    required = [
        "timestamp", "session_id", "platform", "query_strings", "collections_searched",
        "candidates", "selected_ids", "dropped_ids", "pre_dedup_count",
        "post_dedup_count", "model", "input_chars", "output_chars", "latency_ms",
        "fallback_path_taken", "fallback_reason", "output_sha256", "previous_block_sha256", "diff_summary",
    ]
    selected_user = any(
        c.get("id") in set(receipt.get("selected_ids") or [])
        and (c.get("source_metadata") or {}).get("target") == "user"
        and c.get("durability_label") == "durable"
        for c in receipt.get("candidates") or []
    )
    receipt_has_gateway_key = "gateway_session_key" in receipt
    ok = (
        bool(block)
        and len(block) <= 2200
        and all(k in receipt for k in required)
        and before == after
        and receipt.get("model") == "qwen2.5:7b"
        and receipt.get("fallback_path_taken") is False
        and int(receipt.get("latency_ms") or 999999) <= 8000
        and selected_user
        and any(d.get("reason") in {"duplicate", "superseded"} for d in (receipt.get("dropped_ids") or []))
        and receipt_has_gateway_key
        and len(receipt.get("selected_ids") or []) <= 8
    )
    result = {
        "ok": ok,
        "collection_count": count,
        "block_chars": len(block),
        "fallback_path_taken": receipt.get("fallback_path_taken"),
        "fallback_reason": receipt.get("fallback_reason"),
        "model": receipt.get("model"),
        "latency_ms": receipt.get("latency_ms"),
        "receipt_fields_present": all(k in receipt for k in required),
        "selected_user_durable": selected_user,
        "receipt_has_gateway_session_key": receipt_has_gateway_key,
        "memory_user_sha_unchanged": before == after,
        "selected_ids_count": len(receipt.get("selected_ids") or []),
        "dropped_duplicate_count": sum(1 for d in (receipt.get("dropped_ids") or []) if d.get("reason") in {"duplicate", "superseded"}),
        "receipt_path": str(receipt_path),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
