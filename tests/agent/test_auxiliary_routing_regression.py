from unittest.mock import patch

from agent.auxiliary_client import _resolve_task_provider_model


def test_auxiliary_routing_prefers_gemini_for_vision_and_sidecars():
    config = {
        "auxiliary": {
            "vision": {"provider": "gemini", "model": "gemini-3-flash-preview"},
            "web_extract": {"provider": "gemini", "model": "gemini-3.1-flash-lite-preview"},
            "compression": {"provider": "gemini", "model": "gemini-3.1-flash-lite-preview"},
            "session_search": {"provider": "gemini", "model": "gemini-3.1-flash-lite-preview"},
            "approval": {"provider": "gemini", "model": "gemini-3.1-flash-lite-preview"},
            "mcp": {"provider": "gemini", "model": "gemini-3.1-flash-lite-preview"},
            "flush_memories": {"provider": "gemini", "model": "gemini-3.1-flash-lite-preview"},
        }
    }
    with patch("hermes_cli.config.load_config", return_value=config):
        assert _resolve_task_provider_model("vision")[:2] == ("gemini", "gemini-3-flash-preview")
        assert _resolve_task_provider_model("web_extract")[:2] == ("gemini", "gemini-3.1-flash-lite-preview")
        assert _resolve_task_provider_model("compression")[:2] == ("gemini", "gemini-3.1-flash-lite-preview")
        assert _resolve_task_provider_model("session_search")[:2] == ("gemini", "gemini-3.1-flash-lite-preview")
        assert _resolve_task_provider_model("approval")[:2] == ("gemini", "gemini-3.1-flash-lite-preview")
        assert _resolve_task_provider_model("mcp")[:2] == ("gemini", "gemini-3.1-flash-lite-preview")
        assert _resolve_task_provider_model("flush_memories")[:2] == ("gemini", "gemini-3.1-flash-lite-preview")
