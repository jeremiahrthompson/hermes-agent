from __future__ import annotations

import json
from pathlib import Path


def test_import_has_no_chromadb_side_effect(monkeypatch):
    import sys

    sys.modules.pop("plugins.memory.chromadb.g1b_observability", None)
    sys.modules.pop("chromadb", None)
    import plugins.memory.chromadb.g1b_observability as g1b

    assert g1b.FEEDBACK_SCHEMA_VERSION == 1
    assert "chromadb" not in sys.modules


def test_missing_boot_receipts_empty_summary_without_creating_file(tmp_path):
    from plugins.memory.chromadb.g1b_observability import iter_boot_receipts, summarize_boot_receipts

    path = tmp_path / "logs" / "boot_synthesis.jsonl"
    records = list(iter_boot_receipts(path))
    summary = summarize_boot_receipts(records)

    assert records == []
    assert summary["receipt_count"] == 0
    assert summary["malformed_count"] == 0
    assert summary["missing"] is True
    assert not path.exists()


def test_existing_all_malformed_boot_receipts_report_malformed_not_missing(tmp_path):
    from plugins.memory.chromadb.g1b_observability import read_boot_receipts, summarize_boot_receipts

    path = tmp_path / "boot_synthesis.jsonl"
    path.write_text("{bad\n[]\n", encoding="utf-8")

    records = read_boot_receipts(path)
    summary = summarize_boot_receipts(records)

    assert list(records) == []
    assert summary["missing"] is False
    assert summary["receipt_count"] == 0
    assert summary["malformed_count"] == 2


def test_existing_empty_boot_receipts_report_not_missing(tmp_path):
    from plugins.memory.chromadb.g1b_observability import read_boot_receipts, summarize_boot_receipts

    path = tmp_path / "boot_synthesis.jsonl"
    path.write_text("\n", encoding="utf-8")

    summary = summarize_boot_receipts(read_boot_receipts(path))

    assert summary["missing"] is False
    assert summary["receipt_count"] == 0
    assert summary["malformed_count"] == 0

def _g1a_candidate(candidate_id: str, *, source: str, target: str, durability: str, collection: str = "agent_memories") -> dict:
    from plugins.memory.chromadb.g1a import candidate_receipt

    row = {
        "id": candidate_id,
        "metadata": {
            "source": source,
            "target": target,
            "collection": collection,
            "stored_at": 123.0,
            "valid_until": "",
        },
        "composite_score": 0.9,
        "similarity": 0.8,
        "recency": 0.7,
        "source_quality": 0.6,
        "importance": 0.5,
        "durability_label": durability,
    }
    receipt = candidate_receipt(row)
    receipt["source_metadata"]["collection"] = collection
    return receipt


def test_boot_receipt_summary_skips_malformed_and_rolls_up_real_g1a_shape(tmp_path):
    from plugins.memory.chromadb.g1b_observability import iter_boot_receipts, summarize_boot_receipts

    path = tmp_path / "boot_synthesis.jsonl"
    rows = [
        {
            "session_id": "s1",
            "model": "qwen2.5:7b",
            "fallback_path_taken": False,
            "latency_ms": 1200,
            "output_sha256": "abc",
            "selected_ids": ["u1"],
            "dropped_ids": [{"id": "d1", "reason": "duplicate"}],
            "candidates": [
                _g1a_candidate("u1", source="builtin_mirror", target="user", durability="durable"),
                _g1a_candidate("d1", source="pre_compress_extraction", target="memory", durability="ephemeral"),
            ],
        },
        "{not-json",
        {
            "session_id": "s2",
            "model": "legacy",
            "fallback_path_taken": True,
            "fallback_reason": "model_timeout",
            "latency_ms": 8000,
            "output_sha256": "def",
            "selected_ids": [],
            "dropped_ids": ["x1"],
            "candidates": [],
        },
    ]
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(rows[0]) + "\n")
        f.write(rows[1] + "\n")
        f.write(json.dumps(rows[2]) + "\n")

    records = list(iter_boot_receipts(path))
    summary = summarize_boot_receipts(records)

    assert len(records) == 2
    assert summary["receipt_count"] == 2
    assert summary["malformed_count"] == 1
    assert summary["fallback_count"] == 1
    assert summary["fallback_reasons"] == {"model_timeout": 1}
    assert summary["models"] == {"qwen2.5:7b": 1, "legacy": 1}
    assert summary["latency_ms"] == {"count": 2, "min": 1200, "max": 8000, "avg": 4600.0}
    assert summary["selected_ids"] == {"u1": 1}
    assert summary["dropped_ids"] == {"d1": 1, "x1": 1}
    assert summary["drop_reasons"] == {"duplicate": 1, "unknown": 1}
    assert summary["sources"] == {"builtin_mirror": 1, "pre_compress_extraction": 1}
    assert summary["durability"] == {"durable": 1, "ephemeral": 1}
    assert summary["output_hashes"] == {"abc": 1, "def": 1}


def test_extract_correction_markers_structured_no_raw_text():
    from plugins.memory.chromadb.g1b_observability import extract_correction_markers

    text = "You know this; we already discussed this. Why are you asking? Same as before. Don't remember that."
    markers = extract_correction_markers(text)
    labels = {m["label"] for m in markers}

    assert labels == {"you_know_this", "already_discussed", "why_asking", "same_as_before", "dont_remember"}
    for marker in markers:
        assert set(marker) == {"label", "start", "end", "span_sha256"}
        assert isinstance(marker["start"], int)
        assert isinstance(marker["end"], int)
        assert len(marker["span_sha256"]) == 64
        assert "text" not in marker
        assert "raw" not in marker


def test_feedback_ledger_append_read_and_partial_line_tolerance(tmp_path):
    from plugins.memory.chromadb.g1b_observability import append_feedback_event, iter_feedback_events, summarize_feedback_events

    path = tmp_path / "logs" / "memory_feedback.jsonl"
    event = append_feedback_event(
        path,
        event_type="correction_marker",
        session_id="session-1",
        platform="cli",
        labels=["you_know_this"],
        context="You know this already",
        fact_id="",
    )
    with path.open("ab") as f:
        f.write(b'{"partial":')

    events = list(iter_feedback_events(path))
    summary = summarize_feedback_events(events)

    assert len(events) == 1
    assert events[0]["schema_version"] == 1
    assert events[0]["event_type"] == "correction_marker"
    assert events[0]["session_id"] == "session-1"
    assert events[0]["labels"] == ["you_know_this"]
    assert events[0]["context_sha256"] == event["context_sha256"]
    assert "context" not in events[0]
    assert summary["event_count"] == 1
    assert summary["malformed_count"] == 1
    assert summary["event_types"] == {"correction_marker": 1}
    assert summary["labels"] == {"you_know_this": 1}


def test_feedback_events_from_boot_receipt_selected_and_dropped_real_g1a_shape():
    from plugins.memory.chromadb.g1b_observability import feedback_events_from_boot_receipt

    receipt = {
        "session_id": "sid",
        "platform": "cli",
        "selected_ids": ["a"],
        "dropped_ids": [{"id": "b", "reason": "duplicate"}, {"id": "c", "reason": "ephemeral"}],
        "candidates": [
            _g1a_candidate("a", source="builtin_mirror", target="user", durability="durable"),
            _g1a_candidate("b", source="seed", target="memory", durability="durable"),
            _g1a_candidate("c", source="session_turn", target="memory", durability="ephemeral", collection="session_history"),
        ],
    }

    events = feedback_events_from_boot_receipt(receipt)

    assert [e["event_type"] for e in events] == ["boot_selected", "boot_dropped", "boot_dropped"]
    assert events[0]["fact_id"] == "a"
    assert events[0]["collection"] == "agent_memories"
    assert events[0]["source"] == "builtin_mirror"
    assert events[0]["target"] == "user"
    assert events[0]["labels"] == ["durable"]
    assert events[1]["fact_id"] == "b"
    assert set(events[1]["labels"]) == {"durable", "duplicate"}
    assert events[2]["collection"] == "session_history"


def test_g1a_receipt_writer_appends_feedback_best_effort(tmp_path, monkeypatch):
    from plugins.memory.chromadb.g1a import BootSynthesisReceiptWriter
    from plugins.memory.chromadb.g1b_observability import iter_feedback_events

    writer = BootSynthesisReceiptWriter(str(tmp_path))
    receipt = {
        "timestamp": "2026-01-01T00:00:00Z",
        "session_id": "sid",
        "platform": "cli",
        "gateway_session_key": None,
        "query_strings": [],
        "collections_searched": [],
        "candidates": [_g1a_candidate("x", source="seed", target="user", durability="durable")],
        "selected_ids": ["x"],
        "dropped_ids": [],
        "pre_dedup_count": 1,
        "post_dedup_count": 1,
        "model": "qwen2.5:7b",
        "input_chars": 10,
        "output_chars": 5,
        "latency_ms": 1,
        "fallback_path_taken": False,
        "fallback_reason": "",
        "output_sha256": "abc",
        "previous_block_sha256": None,
        "diff_summary": {"lines_changed": 1},
    }

    assert writer.append_once(receipt, guard_key="sid") is True
    events = list(iter_feedback_events(tmp_path / "logs" / "memory_feedback.jsonl"))
    assert len(events) == 1
    assert events[0]["event_type"] == "boot_selected"
    assert events[0]["fact_id"] == "x"


def test_provider_on_turn_start_correction_marker_does_not_touch_chroma_or_flat_files(tmp_path, monkeypatch):
    from plugins.memory.chromadb import ChromaDBMemoryProvider
    from plugins.memory.chromadb.g1b_observability import iter_feedback_events

    hermes_home = tmp_path / ".hermes"
    memories = hermes_home / "memories"
    memories.mkdir(parents=True)
    memory_path = memories / "MEMORY.md"
    user_path = memories / "USER.md"
    memory_path.write_text("memory", encoding="utf-8")
    user_path.write_text("user", encoding="utf-8")
    before = {memory_path: memory_path.read_bytes(), user_path: user_path.read_bytes()}

    provider = ChromaDBMemoryProvider()
    provider._hermes_home = str(hermes_home)
    provider._session_id = "sid"
    provider._platform = "cli"
    provider._gateway_session_key = None
    provider._available = False

    provider.on_turn_start(1, "Why are you asking? You know this.")

    after = {memory_path: memory_path.read_bytes(), user_path: user_path.read_bytes()}
    assert after == before
    events = list(iter_feedback_events(hermes_home / "logs" / "memory_feedback.jsonl"))
    assert len(events) == 2
    assert {e["event_type"] for e in events} == {"correction_marker"}
    assert {tuple(e["labels"]) for e in events} == {("why_asking",), ("you_know_this",)}
