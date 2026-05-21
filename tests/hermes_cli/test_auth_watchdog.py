from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from hermes_cli import auth_watchdog


def _write_profile(root: Path, name: str, model_provider: str | None = None, delegation_provider: str | None = None) -> Path:
    profile_dir = root / name
    profile_dir.mkdir(parents=True)
    cfg: dict = {}
    if model_provider:
        cfg["model"] = {"provider": model_provider, "default": "example-model"}
    if delegation_provider:
        cfg["delegation"] = {"provider": delegation_provider, "model": "example-delegation"}
    (profile_dir / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return profile_dir


def test_parse_auth_status_classifies_logged_in_logged_out_and_unknown():
    assert auth_watchdog.parse_auth_status("openai-codex: logged in") == "logged_in"
    assert auth_watchdog.parse_auth_status("nous: logged out (No credentials stored)") == "logged_out"
    assert auth_watchdog.parse_auth_status("not logged in") == "logged_out"
    assert auth_watchdog.parse_auth_status("Not logged in") == "logged_out"
    assert auth_watchdog.parse_auth_status("unauthenticated") == "logged_out"
    assert auth_watchdog.parse_auth_status("not authenticated") == "logged_out"
    assert auth_watchdog.parse_auth_status("unexpected provider output") == "unknown"


def test_discover_profiles_ignores_state_directories(tmp_path: Path):
    root = tmp_path / "profiles"
    _write_profile(root, "scout", model_provider="openai-codex")
    _write_profile(root, "thudbot", model_provider="openai-codex")
    (root / "logs").mkdir()
    (root / "sessions").mkdir()

    assert auth_watchdog.discover_profiles(root) == ["scout", "thudbot"]


def test_required_configured_provider_logged_out_is_failure(tmp_path: Path):
    root = tmp_path / "profiles"
    _write_profile(root, "scout", model_provider="openai-codex")

    def fake_run(argv, env=None, timeout=0, cwd=None):
        assert "login" not in argv
        assert "setup" not in argv
        return subprocess.CompletedProcess(argv, 0, "openai-codex: logged out\n", "")

    report = auth_watchdog.check_host(
        host="testhost",
        profile_root=root,
        providers=("openai-codex", "qwen-oauth"),
        runner=fake_run,
        hermes_bin="hermes",
        check_claude=False,
    )

    assert report["summary"]["fail"] == 1
    profile = report["profiles"][0]
    assert profile["profile"] == "scout"
    assert profile["severity"] == "fail"
    assert "model.provider openai-codex is logged_out" in profile["notes"]


def test_unused_logged_out_provider_is_warning_not_failure(tmp_path: Path):
    root = tmp_path / "profiles"
    _write_profile(root, "librarian", model_provider="openrouter")

    def fake_run(argv, env=None, timeout=0, cwd=None):
        provider = argv[-1]
        return subprocess.CompletedProcess(argv, 0, f"{provider}: logged out\n", "")

    report = auth_watchdog.check_host(
        host="testhost",
        profile_root=root,
        providers=("openai-codex", "qwen-oauth"),
        runner=fake_run,
        hermes_bin="hermes",
        check_claude=False,
    )

    assert report["summary"]["fail"] == 0
    assert report["summary"]["warn"] == 1
    assert report["profiles"][0]["severity"] == "warn"


def test_delegation_provider_logged_out_is_failure(tmp_path: Path):
    root = tmp_path / "profiles"
    _write_profile(root, "strategist", model_provider="openrouter", delegation_provider="openai-codex")

    def fake_run(argv, env=None, timeout=0, cwd=None):
        return subprocess.CompletedProcess(argv, 0, "openai-codex: logged out\n", "")

    report = auth_watchdog.check_host(
        host="testhost",
        profile_root=root,
        providers=("openai-codex",),
        runner=fake_run,
        hermes_bin="hermes",
        check_claude=False,
    )

    assert report["summary"]["fail"] == 1
    assert "delegation.provider openai-codex is logged_out" in report["profiles"][0]["notes"]


def test_claude_cli_required_by_profile_fails_when_logged_out(tmp_path: Path):
    root = tmp_path / "profiles"
    profile_dir = _write_profile(root, "claudemax")
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump({"model": {"provider": "claude-cli", "default": "claude-opus-4-7"}}),
        encoding="utf-8",
    )

    def fake_run(argv, env=None, timeout=0, cwd=None):
        if argv[:3] == ["claude", "auth", "status"]:
            return subprocess.CompletedProcess(argv, 1, '{"loggedIn": false}', "")
        return subprocess.CompletedProcess(argv, 0, "openai-codex: logged in", "")

    report = auth_watchdog.check_host(
        host="testhost",
        profile_root=root,
        providers=("openai-codex",),
        runner=fake_run,
        hermes_bin="hermes",
        claude_bin="claude",
        check_claude=True,
    )

    assert report["summary"]["fail"] == 1
    assert "model.provider claude-cli is logged_out" in report["profiles"][0]["notes"]


def test_disabled_coding_dispatch_primary_is_not_required(tmp_path: Path):
    root = tmp_path / "profiles"
    profile_dir = _write_profile(root, "thudbot", model_provider="openai-codex")
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"provider": "openai-codex", "default": "gpt-5.5"},
                "delegation": {
                    "provider": "openai-codex",
                    "coding_dispatch": {"enabled": False, "primary": "claude-code-cli"},
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_run(argv, env=None, timeout=0, cwd=None):
        if argv[:3] == ["claude", "auth", "status"]:
            return subprocess.CompletedProcess(argv, 1, '{"loggedIn": false}', "")
        return subprocess.CompletedProcess(argv, 0, "openai-codex: logged in", "")

    report = auth_watchdog.check_host(
        host="testhost",
        profile_root=root,
        providers=("openai-codex",),
        runner=fake_run,
        check_claude=True,
    )

    assert report["summary"]["fail"] == 0
    assert all("claude-cli" not in note for note in report["profiles"][0]["notes"])


def test_claude_check_strips_api_key_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "paid-api-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "paid-auth-token")
    seen_env = {}

    def fake_run(argv, env=None, timeout=0, cwd=None):
        seen_env.update(env or {})
        assert argv == ["claude", "auth", "status"]
        return subprocess.CompletedProcess(argv, 0, "Logged in\n", "")

    result = auth_watchdog.check_claude_cli(fake_run, claude_bin="claude")

    assert result["status"] == "logged_in"
    assert "ANTHROPIC_API_KEY" not in seen_env
    assert "ANTHROPIC_AUTH_TOKEN" not in seen_env
    assert result["checked_without_api_key_env"] is True


def test_claude_check_parses_json_logged_in_without_exposing_identity():
    def fake_run(argv, env=None, timeout=0, cwd=None):
        return subprocess.CompletedProcess(
            argv,
            0,
            '{"loggedIn": true, "email": "person@example.com", "orgId": "org-secret"}',
            "",
        )

    result = auth_watchdog.check_claude_cli(fake_run, claude_bin="claude")

    assert result["status"] == "logged_in"
    assert "person@example.com" not in result["output"]
    assert "org-secret" not in result["output"]


def test_report_redacts_secret_like_output(tmp_path: Path):
    root = tmp_path / "profiles"
    _write_profile(root, "scout", model_provider="openai-codex")

    def fake_run(argv, env=None, timeout=0, cwd=None):
        return subprocess.CompletedProcess(argv, 1, "", "refresh_token=abc123SECRET")

    report = auth_watchdog.check_host(
        host="testhost",
        profile_root=root,
        providers=("openai-codex",),
        runner=fake_run,
        hermes_bin="hermes",
        check_claude=False,
    )

    text = yaml.safe_dump(report)
    assert "abc123SECRET" not in text
    assert "[REDACTED]" in text


def test_empty_profile_root_is_warn_not_error(tmp_path: Path):
    report = auth_watchdog.check_host(
        host="forge",
        profile_root=tmp_path / "missing-profiles",
        providers=("openai-codex",),
        runner=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
        check_claude=False,
    )

    assert report["profiles"] == []
    assert report["summary"]["warn"] == 1
    assert "no profiles found" in report["host_notes"]
