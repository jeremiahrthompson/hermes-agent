from __future__ import annotations

import json
from argparse import Namespace


def test_memory_receipts_human_summary_reads_logs_without_mutation(tmp_path, monkeypatch, capsys):
    from hermes_cli.memory_setup import memory_command

    home = tmp_path / ".hermes"
    logs = home / "logs"
    logs.mkdir(parents=True)
    receipt_path = logs / "boot_synthesis.jsonl"
    receipt_path.write_text(
        json.dumps({
            "session_id": "s1",
            "model": "qwen2.5:7b",
            "fallback_path_taken": False,
            "latency_ms": 42,
            "output_sha256": "abc",
            "selected_ids": ["fact1"],
            "dropped_ids": [],
            "candidates": [{"id": "fact1", "metadata": {"source": "builtin_mirror"}, "durability_label": "durable"}],
        }) + "\n",
        encoding="utf-8",
    )
    before = receipt_path.read_bytes()
    monkeypatch.setenv("HERMES_HOME", str(home))

    args = Namespace(memory_command="receipts", json=False, limit=10)
    memory_command(args)

    out = capsys.readouterr().out
    assert "Boot synthesis receipts" in out
    assert "receipt_count: 1" in out
    assert "qwen2.5:7b" in out
    assert receipt_path.read_bytes() == before
    assert not (logs / "memory_feedback.jsonl").exists()


def test_memory_receipts_json_absent_file_is_stable(tmp_path, monkeypatch, capsys):
    from hermes_cli.memory_setup import memory_command

    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    args = Namespace(memory_command="receipts", json=True, limit=10)
    memory_command(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["receipt_count"] == 0
    assert payload["missing"] is True
    assert payload["malformed_count"] == 0
    assert not (home / "logs" / "boot_synthesis.jsonl").exists()


def test_memory_receipts_limit_truncates_and_zero_means_zero_records(tmp_path, monkeypatch, capsys):
    from hermes_cli.memory_setup import memory_command

    home = tmp_path / ".hermes"
    logs = home / "logs"
    logs.mkdir(parents=True)
    receipt_path = logs / "boot_synthesis.jsonl"
    receipt_path.write_text(
        "".join(
            json.dumps({
                "session_id": f"s{i}",
                "model": "qwen2.5:7b",
                "fallback_path_taken": False,
                "latency_ms": i,
                "output_sha256": f"hash{i}",
                "selected_ids": [f"fact{i}"],
                "dropped_ids": [],
                "candidates": [],
            }) + "\n"
            for i in range(3)
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    memory_command(Namespace(memory_command="receipts", json=True, limit=2))
    limited = json.loads(capsys.readouterr().out)
    assert limited["receipt_count"] == 2
    assert limited["selected_ids"] == {"fact1": 1, "fact2": 1}

    memory_command(Namespace(memory_command="receipts", json=True, limit=0))
    zero = json.loads(capsys.readouterr().out)
    assert zero["receipt_count"] == 0
    assert zero["selected_ids"] == {}
    assert zero["missing"] is False
