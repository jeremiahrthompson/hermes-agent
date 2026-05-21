import json

import pytest

from plugins.memory.chromadb import ChromaDBMemoryProvider


def test_chromadb_tool_schemas_exposed_before_runtime_available():
    provider = ChromaDBMemoryProvider()
    provider._available = False

    schemas = provider.get_tool_schemas()

    assert {schema["name"] for schema in schemas} == {"team_memory", "vector_search"}

    result = json.loads(provider.handle_tool_call("vector_search", {"query": "hello"}))
    assert result == {"error": "ChromaDB is not available."}


def test_chromadb_tool_schemas_hidden_in_cron_context():
    provider = ChromaDBMemoryProvider()
    provider._available = False
    provider._cron_skipped = True

    assert provider.get_tool_schemas() == []
    assert json.loads(provider.handle_tool_call("vector_search", {"query": "hello"})) == {
        "error": "ChromaDB is not active (cron context)."
    }


# ---------------------------------------------------------------------------
# Generated profile initialize kwargs (Phase 1)
# ---------------------------------------------------------------------------


def test_initialize_accepts_prompt_source_and_generated_enabled(monkeypatch, tmp_path):
    provider = ChromaDBMemoryProvider()
    # Avoid touching real ChromaDB
    monkeypatch.setattr(provider, "_init_client",
                        lambda: setattr(provider, "_available", True))
    monkeypatch.setattr(provider, "_load_team_context", lambda: None)

    provider.initialize(
        "sess-1",
        hermes_home=str(tmp_path),
        agent_identity="rilo",
        platform="cli",
        agent_context="primary",
        prompt_source="shadow",
        generated_prompt_enabled=True,
    )
    assert provider._prompt_source == "shadow"
    assert provider._generated_profile_enabled is True
    assert provider._agent_context == "primary"


def test_initialize_defaults_legacy_when_kwargs_missing(monkeypatch, tmp_path):
    provider = ChromaDBMemoryProvider()
    monkeypatch.setattr(provider, "_init_client",
                        lambda: setattr(provider, "_available", True))
    monkeypatch.setattr(provider, "_load_team_context", lambda: None)

    provider.initialize(
        "sess-1",
        hermes_home=str(tmp_path),
        agent_identity="rilo",
        platform="cli",
    )
    assert provider._prompt_source == "legacy"
    assert provider._generated_profile_enabled is False


def test_cron_initialize_sets_cron_skipped_and_no_generation(monkeypatch, tmp_path):
    provider = ChromaDBMemoryProvider()

    embed_calls: list = []
    monkeypatch.setattr(provider, "_init_client",
                        lambda: setattr(provider, "_available", True))
    monkeypatch.setattr(provider, "_embed",
                        lambda texts: embed_calls.append(texts) or [[0.0]])

    provider.initialize(
        "sess",
        hermes_home=str(tmp_path),
        agent_identity="rilo",
        platform="cron",
        agent_context="cron",
        prompt_source="provider_with_legacy_fallback",
        generated_prompt_enabled=True,
    )
    assert provider._cron_skipped is True
    assert provider.system_prompt_block() == ""
    assert embed_calls == []
