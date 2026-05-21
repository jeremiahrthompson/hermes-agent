"""G1A boot synthesis contract tests for ChromaDB memory provider.

Pure unit tests: no live ChromaDB, Forge, or Ollama. These tests encode
A1-A11 from docs/memory/G1A_CONTRACT.md using fakes.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest


def _fact(fid: str, content: str, *, target: str = "memory", source: str | None = "builtin_mirror",
          distance: float = 0.1, importance: float = 0.5, stored_at: float | None = None,
          **meta):
    md = {"target": target, "importance": importance, "stored_at": stored_at or time.time()}
    if source is not None:
        md["source"] = source
    md.update(meta)
    return {"id": fid, "content": content, "metadata": md, "distance": distance}


def test_g1a_score_components_use_static_five_signal_formula():
    from plugins.memory.chromadb.g1a import score_result, source_quality_score, durability_score

    now = time.time()
    row = _fact("m1", "User prefers concise terminal-safe responses", stored_at=now, importance=0.8)
    scored = score_result(row, now=now)

    assert scored["similarity"] == pytest.approx(1.0 / 1.1)
    assert scored["recency"] == pytest.approx(1.0)
    assert scored["source_quality"] == source_quality_score(row["metadata"])
    assert scored["durability"] == durability_score("durable")
    assert scored["composite_score"] == pytest.approx(
        0.35 * scored["similarity"]
        + 0.25 * scored["recency"]
        + 0.20 * scored["source_quality"]
        + 0.15 * scored["importance"]
        + 0.05 * scored["durability"]
    )


def test_source_quality_ordering_honors_contract():
    from plugins.memory.chromadb.g1a import source_quality_score

    scores = {
        name: source_quality_score({"source": name})
        for name in ["builtin_mirror", "seed", "pre_compress_extraction", "session_turn"]
    }
    assert scores["builtin_mirror"] > scores["seed"] > scores["pre_compress_extraction"] > scores["session_turn"]
    assert source_quality_score({}) == scores["session_turn"]
    assert source_quality_score({"source": "unknown"}) == scores["session_turn"]


def test_durability_classifier_excludes_ephemeral_and_stale_time_bound():
    from plugins.memory.chromadb.g1a import filter_candidates

    now = time.time()
    rows = [
        _fact("durable", "User prefers concise responses and safe Kanban boundaries", target="user"),
        _fact("ephemeral", "PR #9 merged at commit 4aa4a1a and task phase is done"),
        _fact("stale", "Application to SpaceX is in flight", valid_until=now - 60),
        _fact("valid", "Application to Anduril is active", valid_until=now + 3600),
    ]
    kept, dropped = filter_candidates(rows, now=now)
    assert {r["id"] for r in kept} == {"durable", "valid"}
    assert {d["id"]: d["reason"] for d in dropped} == {
        "ephemeral": "ephemeral",
        "stale": "out_of_validity_window",
    }


def test_content_hash_dedup_collapses_normalized_duplicates():
    from plugins.memory.chromadb.g1a import deduplicate_candidates

    rows = [
        _fact("a", "User prefers concise responses."),
        _fact("b", " user   PREFERS concise responses!!! ", source="pre_compress_extraction"),
    ]
    kept, dropped = deduplicate_candidates(rows, embed_fn=lambda texts: [[1.0, 0.0] for _ in texts])
    assert len(kept) == 1
    assert {d["id"] for d in dropped} == {"b"}
    assert dropped[0]["reason"] == "duplicate"


def test_embedding_near_duplicate_threshold_collapses_at_092():
    from plugins.memory.chromadb.g1a import deduplicate_candidates

    rows = [
        _fact("a", "User likes SpaceX applications tracked as time-bound context", importance=0.9, valid_until=time.time() + 3600),
        _fact("b", "SpaceX application context should be remembered while active", importance=0.8, valid_until=time.time() + 3600),
        _fact("c", "Different durable fact about terminal formatting", importance=0.9),
    ]
    embeddings = [[1.0, 0.0], [0.94, 0.341174], [0.0, 1.0]]
    kept, dropped = deduplicate_candidates(rows, embed_fn=lambda texts: embeddings)
    assert {r["id"] for r in kept} == {"a", "c"}
    assert {d["id"]: d["reason"] for d in dropped} == {"b": "duplicate"}


def test_receipt_writer_appends_once_and_populates_required_fields(tmp_path):
    from plugins.memory.chromadb.g1a import BootSynthesisReceiptWriter, REQUIRED_RECEIPT_FIELDS

    writer = BootSynthesisReceiptWriter(str(tmp_path))
    receipt = writer.base_receipt(session_id="sess", platform="cli", gateway_session_key=None)
    receipt.update({
        "query_strings": ["q"],
        "collections_searched": ["agent_memories"],
        "candidates": [],
        "selected_ids": [],
        "dropped_ids": [],
        "pre_dedup_count": 0,
        "post_dedup_count": 0,
        "model": "legacy",
        "input_chars": 0,
        "output_chars": 5,
        "latency_ms": 1,
        "fallback_path_taken": True,
        "fallback_reason": "kill_switch_off",
        "output_sha256": hashlib.sha256(b"hello").hexdigest(),
        "previous_block_sha256": None,
        "diff_summary": None,
    })
    assert set(REQUIRED_RECEIPT_FIELDS).issubset(receipt)
    writer.append_once(receipt, guard_key="sess:boot")
    writer.append_once(receipt, guard_key="sess:boot")

    lines = (tmp_path / "logs" / "boot_synthesis.jsonl").read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert set(REQUIRED_RECEIPT_FIELDS).issubset(parsed)
    assert "embeddings" not in lines[0]
    assert "vectors" not in lines[0]


def _provider(tmp_path, monkeypatch, *, boot_enabled=True):
    from plugins.memory.chromadb import ChromaDBMemoryProvider
    from plugins.memory.chromadb.config import ChromaDBConfig

    p = ChromaDBMemoryProvider()
    p._config = ChromaDBConfig()
    p._available = True
    p._cron_skipped = False
    p._hermes_home = str(tmp_path)
    p._agent_name = "rilo"
    p._session_id = "sess-g1a"
    p._platform = "cli"
    p._gateway_session_key = None
    p._prompt_source = "provider_with_legacy_fallback"
    p._generated_profile_enabled = True
    p._boot_synthesis_enabled = boot_enabled
    p._team_context = ""
    p._collections = {"memories": object()}
    p._search_for_generated = lambda target, **kwargs: [
        _fact(f"{target}-1", f"Durable {target} fact: user prefers safe reusable systems", target=target),
        _fact(f"{target}-2", "PR #123 was opened yesterday", target=target),
    ]
    monkeypatch.setattr("plugins.memory.chromadb.g1a.synthesize_with_ollama", lambda **kwargs: "<memory-profile source=\"chromadb\" degraded=\"false\">\n- > \"durable synthesized fact\"\n</memory-profile>")
    return p


def test_boot_synthesis_happy_path_returns_non_empty_block_and_receipt(tmp_path, monkeypatch):
    p = _provider(tmp_path, monkeypatch, boot_enabled=True)
    block = p._build_generated_profile_block()
    assert block
    assert len(block) <= 2200
    lines = (tmp_path / "logs" / "boot_synthesis.jsonl").read_text().splitlines()
    assert len(lines) == 1
    receipt = json.loads(lines[0])
    assert receipt["fallback_path_taken"] is False
    assert receipt["model"] == "qwen2.5:7b"
    assert receipt["selected_ids"]
    assert len(receipt["selected_ids"]) <= 8
    assert all(c.get("durability_label") for c in receipt["candidates"])
    for c in receipt["candidates"]:
        assert "composite_score" in c
        assert "stored_at" in c
        assert "target_metadata" in c


def test_boot_synthesis_model_unreachable_falls_back_to_legacy(tmp_path, monkeypatch):
    import plugins.memory.chromadb.g1a as g1a

    p = _provider(tmp_path, monkeypatch, boot_enabled=True)
    def boom(**kwargs):
        raise g1a.ModelUnavailable("ollama down")
    monkeypatch.setattr(g1a, "synthesize_with_ollama", boom)

    block = p._build_generated_profile_block()
    assert "<memory-profile" in block
    receipt = json.loads((tmp_path / "logs" / "boot_synthesis.jsonl").read_text().splitlines()[0])
    assert receipt["fallback_path_taken"] is True
    assert receipt["fallback_reason"] == "model_unreachable"
    assert receipt["model"] == "qwen2.5:7b"


def test_boot_synthesis_timeout_empty_and_unsafe_fallback_reasons(tmp_path, monkeypatch):
    import plugins.memory.chromadb.g1a as g1a

    cases = [
        (g1a.ModelTimeout("slow"), "timeout"),
        (g1a.EmptyOutput("empty"), "empty_output"),
        (g1a.UnsafeOutput("bad"), "unsafe_output"),
    ]
    for exc, reason in cases:
        p = _provider(tmp_path / reason, monkeypatch, boot_enabled=True)
        monkeypatch.setattr(g1a, "synthesize_with_ollama", lambda **kwargs: (_ for _ in ()).throw(exc))
        p._build_generated_profile_block()
        receipt = json.loads(((tmp_path / reason) / "logs" / "boot_synthesis.jsonl").read_text().splitlines()[0])
        assert receipt["fallback_path_taken"] is True
        assert receipt["fallback_reason"] == reason
        assert receipt["model"] == "qwen2.5:7b"


def test_chroma_unavailable_appends_required_receipt(tmp_path):
    from plugins.memory.chromadb import ChromaDBMemoryProvider

    p = ChromaDBMemoryProvider()
    p._available = False
    p._cron_skipped = False
    p._hermes_home = str(tmp_path)
    p._session_id = "sess-unavailable"
    p._platform = "cli"
    p._gateway_session_key = None
    p._prompt_source = "provider_with_legacy_fallback"
    p._generated_profile_enabled = True
    p._boot_synthesis_enabled = True
    p._agent_context = "primary"
    assert p.system_prompt_block() == ""
    receipt = json.loads((tmp_path / "logs" / "boot_synthesis.jsonl").read_text().splitlines()[0])
    assert receipt["fallback_path_taken"] is True
    assert receipt["fallback_reason"] == "chroma_unreachable"
    assert receipt["model"] == "qwen2.5:7b"


def test_no_candidate_fallback_uses_contract_enum_reason(tmp_path, monkeypatch):
    p = _provider(tmp_path, monkeypatch, boot_enabled=True)
    p._search_for_generated = lambda target, **kwargs: []
    block = p._build_generated_profile_block()
    assert "<memory-profile" in block
    receipt = json.loads((tmp_path / "logs" / "boot_synthesis.jsonl").read_text().splitlines()[0])
    assert receipt["fallback_path_taken"] is True
    assert receipt["fallback_reason"] == "exception"
    assert receipt["model"] == "qwen2.5:7b"


def test_boot_synthesis_kill_switch_is_legacy_bit_identical(tmp_path, monkeypatch):
    calls = []
    p_enabled = _provider(tmp_path / "enabled", monkeypatch, boot_enabled=False)
    original = p_enabled._search_for_generated
    def wrapped(target, **kwargs):
        calls.append(kwargs.get("legacy_pre_g1a"))
        return original(target, **kwargs)
    p_enabled._search_for_generated = wrapped
    p_disabled = _provider(tmp_path / "disabled", monkeypatch, boot_enabled=False)
    legacy_a = p_enabled._build_legacy_generated_profile_block()
    block = p_disabled._build_generated_profile_block()
    assert calls == [True, True]
    assert hashlib.sha256(block.encode()).hexdigest() == hashlib.sha256(legacy_a.encode()).hexdigest()
    receipt = json.loads((tmp_path / "disabled" / "logs" / "boot_synthesis.jsonl").read_text().splitlines()[0])
    assert receipt["model"] == "legacy"
    assert receipt["fallback_reason"] == "kill_switch_off"


def test_prompt_profile_exposes_distinct_pre_g1a_legacy_ranking():
    from plugins.memory.chromadb.prompt_profile import rank_facts

    now = time.time()
    rows = [
        _fact("semantic", "generic fact", source="pre_compress_extraction", distance=0.01, importance=0.5, stored_at=now),
        _fact("source", "generic fact two", source="builtin_mirror", distance=0.4, importance=0.5, stored_at=now),
    ]
    assert rank_facts(rows, now=now, legacy_pre_g1a=True)[0]["id"] == "semantic"
    assert rank_facts(rows, now=now, legacy_pre_g1a=False)[0]["id"] == "source"


def test_memory_and_user_files_unchanged_across_boot_synthesis(tmp_path, monkeypatch):
    (tmp_path / "MEMORY.md").write_text("memory facts\n")
    (tmp_path / "USER.md").write_text("user facts\n")
    before = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in ["MEMORY.md", "USER.md"]}
    p = _provider(tmp_path, monkeypatch, boot_enabled=True)
    p._build_generated_profile_block()
    after = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in ["MEMORY.md", "USER.md"]}
    assert before == after


def test_config_default_and_provider_init_bridge(monkeypatch, tmp_path):
    from hermes_cli.config import DEFAULT_CONFIG
    from plugins.memory.chromadb import ChromaDBMemoryProvider

    assert DEFAULT_CONFIG["memory"]["boot_synthesis_enabled"] is True
    p = ChromaDBMemoryProvider()
    monkeypatch.setattr(p, "_init_client", lambda: setattr(p, "_available", True))
    monkeypatch.setattr(p, "_load_team_context", lambda: None)
    p.initialize("sess", hermes_home=str(tmp_path), platform="telegram", gateway_session_key="gkey", boot_synthesis_enabled=False)
    assert p._boot_synthesis_enabled is False
    assert p._platform == "telegram"
    assert p._gateway_session_key == "gkey"


def test_fallback_does_not_suppress_legacy_memory_prompt_policy():
    from tests.run_agent.test_memory_prompt_source import _make_agent, _DEGRADED_PROVIDER_BLOCK

    agent = _make_agent(prompt_source="provider_with_legacy_fallback", external_block=_DEGRADED_PROVIDER_BLOCK)
    parts = agent._build_system_prompt_parts()
    assert "legacy memory entry" in parts["volatile"]
    assert "legacy user entry" in parts["volatile"]
