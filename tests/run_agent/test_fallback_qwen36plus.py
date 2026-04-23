from unittest.mock import patch

from run_agent import AIAgent


def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"{n} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def test_qwen36plus_fallback_is_preserved_as_model_pinned_openrouter_pair():
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://chatgpt.com/backend-api/codex",
            model="gpt-5.4",
            provider="openai-codex",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            fallback_model={"provider": "openrouter", "model": "qwen/qwen3.6-plus"},
        )

    assert agent._fallback_chain == [{"provider": "openrouter", "model": "qwen/qwen3.6-plus"}]
    assert agent._fallback_model == {"provider": "openrouter", "model": "qwen/qwen3.6-plus"}
