"""Claude CLI adapter for Hermes Agent.

Dispatches autonomous Claude Code agents via the local Claude CLI (v2.1+)
using the authenticated OAuth Max session. The CLI is invoked as a subprocess
with --output-format json, parsing the JSON response to extract result, usage,
and modelUsage.

Auth: No API key needed — the Claude CLI uses its own OAuth session stored in
~/.claude/.credentials.json (Max subscription) or ~/.claude.json (API key).

Usage:
    from agent.claude_cli_adapter import dispatch_claude_agent
    result = dispatch_claude_agent("Fix the failing test", cwd="/path/to/project")
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _resolve_claude_cli_path() -> str:
    """Resolve the path to the claude CLI executable."""
    env_path = os.getenv("HERMES_CLAUDE_CLI_PATH", "").strip()
    if env_path:
        return env_path
    found = shutil.which("claude")
    if found:
        return found
    for cmd in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if os.path.isfile(cmd) and os.access(cmd, os.X_OK):
            return cmd
    return "claude"


def _build_claude_cli_command(
    prompt: str,
    model: str,
    max_turns: int = 0,
    system_prompt: Optional[str] = None,
    cwd: Optional[str] = None,
) -> tuple[List[str], Optional[str]]:
    """Build the claude CLI command for subprocess invocation.

    Agent mode only — full Claude Code capabilities with --dangerously-skip-permissions.
    """
    cmd = [_resolve_claude_cli_path()]
    cmd.append("-p")

    if system_prompt:
        cmd.append(f"--system-prompt={system_prompt}")

    cmd.append(prompt)
    cmd.append("--output-format=json")

    if model:
        cmd.append(f"--model={model}")

    cmd.append(f"--max-turns={max_turns}")
    effective_cwd = cwd
    cmd.append("--dangerously-skip-permissions")
    cmd.append("--no-session-persistence")

    return cmd, effective_cwd


def _parse_json_response(raw_output: str) -> Dict[str, Any]:
    """Parse and validate the Claude CLI JSON response."""
    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Claude CLI JSON output: %s\nOutput: %s", e, raw_output[:500])
        raise

    if not isinstance(data, dict):
        raise ValueError(f"Expected dict from Claude CLI, got {type(data).__name__}")

    msg_type = data.get("type", "")
    if msg_type == "error" or data.get("is_error", False):
        error_msg = data.get("error", {}).get("message", str(data.get("result", "")))
        raise RuntimeError(f"Claude CLI error: {error_msg}")

    if msg_type != "result":
        raise ValueError(f"Unexpected Claude CLI response type: {msg_type}")

    return data


def _extract_usage_from_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract token usage from Claude CLI response."""
    usage = data.get("usage", {})
    model_usage = data.get("modelUsage", {})

    primary_usage = {}
    if model_usage:
        for model_name, model_data in model_usage.items():
            if isinstance(model_data, dict) and model_data.get("inputTokens", 0) > 0:
                primary_usage = model_data
                break

    result = {
        "input_tokens": usage.get("input_tokens", primary_usage.get("inputTokens", 0)),
        "output_tokens": usage.get("output_tokens", primary_usage.get("outputTokens", 0)),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "total_cost_usd": data.get("total_cost_usd", 0.0),
        "server_tool_use": usage.get("server_tool_use", {}),
    }

    if primary_usage:
        result["input_tokens"] = primary_usage.get("inputTokens", result["input_tokens"])
        result["output_tokens"] = primary_usage.get("outputTokens", result["output_tokens"])

    return result


def _build_simple_namespace_response(
    data: Dict[str, Any],
    model: str,
) -> SimpleNamespace:
    """Build a SimpleNamespace response matching Hermes's expected response shape."""
    usage = _extract_usage_from_response(data)

    result_text = data.get("result", "")
    stop_reason = data.get("stop_reason", "end_turn")

    finish_reason_map = {
        "end_turn": "stop",
        "max_turns": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }
    finish_reason = finish_reason_map.get(stop_reason, "stop")

    message = SimpleNamespace(
        role="assistant",
        content=result_text,
        tool_calls=None,
        reasoning_content=None,
    )

    choice = SimpleNamespace(
        index=0,
        message=message,
        finish_reason=finish_reason,
    )

    response_id = f"claude-cli-{data.get('session_id', 'unknown')[:8]}"

    return SimpleNamespace(
        id=response_id,
        model=model,
        choices=[choice],
        usage=SimpleNamespace(
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        ),
        _raw_response=data,
        _claude_session_id=data.get("session_id", ""),
        _claude_usage=usage,
    )


def dispatch_claude_agent(
    prompt: str,
    cwd: Optional[str] = None,
    max_turns: int = 0,
    model: str = "claude-opus-4-6",
    system_prompt: Optional[str] = None,
) -> SimpleNamespace:
    """Dispatch Claude Code as an autonomous agent with full tool access.

    Gives Claude Code full capabilities: bash, file read/write, web search,
    MCP tools, everything. Use this when Hermes needs to dispatch autonomous
    coding work.

    This is NOT wired into Hermes model routing — call it explicitly when needed.

    Args:
        prompt: The task prompt for Claude Code.
        cwd: Working directory for the agent (where it reads/writes files).
            Defaults to current working directory.
        max_turns: Maximum agentic turns. 0 = unlimited (default).
        model: Model to use (default claude-opus-4-6).
        system_prompt: Optional system prompt override.

    Returns:
        SimpleNamespace with the agent's final result.

    Example:
        result = dispatch_claude_agent(
            "Fix the failing test in tests/test_parser.py",
            cwd="/home/user/project",
        )
        print(result.choices[0].message.content)
    """
    cmd, effective_cwd = _build_claude_cli_command(
        prompt=prompt,
        model=model,
        max_turns=max_turns,
        system_prompt=system_prompt,
        cwd=cwd,
    )

    env = os.environ.copy()
    if system_prompt and len(system_prompt) > 500:
        env["HERMES_CLAUDE_SYSTEM_PROMPT"] = system_prompt

    logger.debug("Dispatching Claude agent [cwd=%s]: %s", effective_cwd, prompt[:100])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
            cwd=effective_cwd,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude CLI agent timed out after 15 minutes")
    except FileNotFoundError:
        raise RuntimeError(
            "Claude CLI not found. Ensure 'claude' is installed and in PATH, "
            "or set HERMES_CLAUDE_CLI_PATH to the full path."
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("Claude CLI agent exited with code %d: %s", result.returncode, stderr)
        raise RuntimeError(f"Claude CLI agent failed: {stderr or result.stdout or 'unknown error'}")

    data = _parse_json_response(result.stdout)
    return _build_simple_namespace_response(data, model=model)


def resolve_claude_cli_token() -> str:
    """Check if Claude CLI is authenticated (no token needed).

    The Claude CLI uses its own OAuth session stored in ~/.claude/.credentials.json.
    No API key is required for this transport.
    """
    cli_path = _resolve_claude_cli_path()
    try:
        result = subprocess.run(
            [cli_path, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return "cli-authenticated"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return ""
