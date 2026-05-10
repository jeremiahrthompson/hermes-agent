from types import SimpleNamespace
from unittest.mock import patch
import json

from tools import delegate_tool


def _parent_agent():
    return SimpleNamespace(
        _delegate_depth=0,
        _memory_manager=None,
        provider="openai-codex",
        enabled_toolsets=["terminal", "file", "web"],
        valid_tool_names={"terminal", "read_file", "web_search"},
        api_key="test-key",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        acp_command=None,
        acp_args=[],
        max_tokens=None,
        reasoning_config=None,
        prefill_messages=None,
        platform="cli",
        session_id="parent-session",
        providers_allowed=None,
        providers_ignored=None,
        providers_order=None,
        provider_sort=None,
        tool_progress_callback=None,
        _session_db=None,
        _active_children=[],
        _active_children_lock=None,
    )


def test_delegate_task_prefers_claude_code_for_coding_tasks():
    parent = _parent_agent()
    with (
        patch.object(delegate_tool, "_load_config", return_value={
            "max_iterations": 50,
            "coding_dispatch": {
                "enabled": True,
                "primary_model": "claude-opus-4-6",
                "backup_provider": "openai-codex",
                "backup_model": "gpt-5.4",
            },
        }),
        patch.object(delegate_tool, "_resolve_delegation_credentials", return_value={
            "model": None,
            "provider": None,
            "base_url": None,
            "api_key": None,
            "api_mode": None,
        }),
        patch.object(delegate_tool, "_resolve_coding_backup_runtime", return_value={
            "provider": "openai-codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_key": "codex-key",
            "api_mode": "codex_responses",
            "command": None,
            "args": [],
            "model": "gpt-5.4",
        }),
        patch.object(delegate_tool, "_run_claude_code_task", return_value={
            "task_index": 0,
            "status": "completed",
            "summary": "done",
            "error": None,
            "api_calls": 1,
            "duration_seconds": 0.1,
            "backend": "claude-code-cli",
        }) as mock_claude,
        patch.object(delegate_tool, "_build_child_agent") as mock_build_child,
    ):
        raw = delegate_tool.delegate_task(
            goal="Implement the authentication module and fix failing tests",
            toolsets=["terminal", "file"],
            parent_agent=parent,
        )

    result = json.loads(raw)
    assert result["results"][0]["backend"] == "claude-code-cli"
    mock_claude.assert_called_once()
    mock_build_child.assert_not_called()



def test_non_coding_delegate_task_uses_normal_subagent_path():
    parent = _parent_agent()
    fake_child = SimpleNamespace()
    with (
        patch.object(delegate_tool, "_load_config", return_value={
            "max_iterations": 50,
            "coding_dispatch": {
                "enabled": True,
                "primary_model": "claude-opus-4-6",
                "backup_provider": "openai-codex",
                "backup_model": "gpt-5.4",
            },
        }),
        patch.object(delegate_tool, "_resolve_delegation_credentials", return_value={
            "model": None,
            "provider": None,
            "base_url": None,
            "api_key": None,
            "api_mode": None,
        }),
        patch.object(delegate_tool, "_resolve_coding_backup_runtime", return_value=None),
        patch.object(delegate_tool, "_build_child_agent", return_value=fake_child) as mock_build_child,
        patch.object(delegate_tool, "_run_single_child", return_value={
            "task_index": 0,
            "status": "completed",
            "summary": "normal subagent ok",
            "error": None,
            "api_calls": 1,
            "duration_seconds": 0.1,
        }) as mock_run_child,
        patch.object(delegate_tool, "_run_claude_code_task") as mock_claude,
    ):
        raw = delegate_tool.delegate_task(
            goal="Research the latest Selenium API docs and summarize the changes",
            toolsets=["web"],
            parent_agent=parent,
        )

    result = json.loads(raw)
    assert result["results"][0]["summary"] == "normal subagent ok"
    mock_build_child.assert_called_once()
    mock_run_child.assert_called_once()
    mock_claude.assert_not_called()



def test_run_claude_code_task_falls_back_to_codex_backup():
    parent = _parent_agent()
    task = {
        "goal": "Refactor the parser and update tests",
        "context": "Project uses pytest.",
        "toolsets": ["terminal", "file"],
    }
    with (
        patch("agent.claude_cli_adapter.dispatch_claude_agent", side_effect=RuntimeError("claude unavailable")),
        patch.object(delegate_tool, "_run_backup_subagent", return_value={
            "task_index": 0,
            "status": "completed",
            "summary": "codex backup ok",
            "error": None,
            "api_calls": 2,
            "duration_seconds": 0.2,
            "backend": "openai-codex-backup",
        }) as mock_backup,
    ):
        result = delegate_tool._run_claude_code_task(
            task_index=0,
            task=task,
            parent_agent=parent,
            primary_model="claude-opus-4-6",
            backup_runtime={
                "provider": "openai-codex",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_key": "***",
                "api_mode": "codex_responses",
                "command": None,
                "args": [],
                "model": "gpt-5.4",
            },
            max_iterations=50,
            saved_parent_tool_names=["terminal", "read_file"],
        )

    assert result["backend"] == "openai-codex-backup"
    assert result["fallback_from"] == "claude-code-cli"
    assert result["error"] == "claude unavailable"
    mock_backup.assert_called_once()
