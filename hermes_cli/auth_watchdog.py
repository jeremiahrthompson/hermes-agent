from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

Runner = Callable[..., subprocess.CompletedProcess]

IGNORED_PROFILE_DIRS = {"cron", "logs", "memories", "sessions", "skills", "workspace"}
DEFAULT_PROVIDERS = ("nous", "openai-codex", "qwen-oauth")
SECRET_RE = re.compile(
    r"(?i)(token|secret|api[_-]?key|authorization|cookie|refresh|access)(\s*[:=]\s*)([^\s,;]+)"
)


def redact(text: Any) -> str:
    value = "" if text is None else str(text)
    return SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", value)


def parse_auth_status(output: str) -> str:
    lowered = output.lower()
    negative_phrases = (
        "logged out",
        "not logged in",
        "credentials not found",
        "not authenticated",
        "unauthenticated",
        "authentication failed",
        "invalid authentication",
    )
    if any(phrase in lowered for phrase in negative_phrases):
        return "logged_out"
    if re.search(r"\blogged in\b", lowered) or re.search(r"\bauthenticated\b", lowered):
        return "logged_in"
    return "unknown"


def parse_claude_auth_status(output: str) -> str:
    try:
        data = json.loads(output)
        if isinstance(data, dict) and data.get("loggedIn") is True:
            return "logged_in"
        if isinstance(data, dict) and data.get("loggedIn") is False:
            return "logged_out"
    except Exception:
        pass
    return parse_auth_status(output)


def summarize_claude_output(output: str) -> str:
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            safe = {
                "loggedIn": data.get("loggedIn"),
                "authMethod": data.get("authMethod"),
                "apiProvider": data.get("apiProvider"),
                "subscriptionType": data.get("subscriptionType"),
            }
            return redact(json.dumps({k: v for k, v in safe.items() if v is not None}, sort_keys=True))
    except Exception:
        pass
    return redact(output)[:500]


def discover_profiles(profile_root: Path) -> list[str]:
    if not profile_root.exists() or not profile_root.is_dir():
        return []
    profiles: list[str] = []
    for child in profile_root.iterdir():
        if not child.is_dir():
            continue
        if child.name in IGNORED_PROFILE_DIRS:
            continue
        if child.name.startswith("."):
            continue
        profiles.append(child.name)
    return sorted(profiles)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        return {"_load_error": redact(exc)}


def load_profile_config(profile_root: Path, profile: str) -> dict[str, Any]:
    return _load_yaml(profile_root / profile / "config.yaml")


def load_default_config() -> dict[str, Any]:
    return _load_yaml(Path.home() / ".hermes" / "config.yaml")


def configured_provider_references(config: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []

    def add(path: str, provider: Any) -> None:
        if isinstance(provider, str) and provider.strip():
            refs.append((path, provider.strip()))

    model = config.get("model")
    if isinstance(model, dict):
        add("model.provider", model.get("provider"))

    delegation = config.get("delegation")
    if isinstance(delegation, dict):
        add("delegation.provider", delegation.get("provider"))
        coding_dispatch = delegation.get("coding_dispatch")
        if isinstance(coding_dispatch, dict):
            if coding_dispatch.get("enabled") is not False:
                add("delegation.coding_dispatch.backup_provider", coding_dispatch.get("backup_provider"))
                primary = coding_dispatch.get("primary")
                if primary == "claude-code-cli":
                    refs.append(("delegation.coding_dispatch.primary", "claude-cli"))

    return refs


def scrub_auth_env(env: dict[str, str] | None = None) -> dict[str, str]:
    clean = dict(os.environ if env is None else env)
    for key in list(clean):
        upper = key.upper()
        if upper.startswith("ANTHROPIC_") or upper in {"CLAUDE_API_KEY", "CLAUDE_AUTH_TOKEN"}:
            clean.pop(key, None)
    return clean


def run_command(argv: list[str], *, env: dict[str, str] | None = None, timeout: int = 15, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
        cwd=cwd,
        check=False,
    )


def hermes_auth_status(
    runner: Runner,
    provider: str,
    *,
    hermes_bin: str = "hermes",
    profile: str | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    argv = [hermes_bin]
    if profile and profile != "default":
        argv.extend(["-p", profile])
    argv.extend(["auth", "status", provider])
    try:
        cp = runner(argv, env=os.environ.copy(), timeout=timeout)
        combined = f"{cp.stdout or ''}\n{cp.stderr or ''}"
        status = parse_auth_status(combined)
        if cp.returncode != 0 and status == "unknown":
            status = "unknown"
        return {
            "provider": provider,
            "status": status,
            "returncode": cp.returncode,
            "output": redact(combined.strip())[:500],
        }
    except subprocess.TimeoutExpired:
        return {"provider": provider, "status": "unknown", "returncode": 124, "output": "timeout"}
    except FileNotFoundError:
        return {"provider": provider, "status": "unknown", "returncode": 127, "output": "hermes command not found"}
    except Exception as exc:
        return {"provider": provider, "status": "unknown", "returncode": 2, "output": redact(exc)}


def check_claude_cli(runner: Runner = run_command, *, claude_bin: str = "claude", timeout: int = 15) -> dict[str, Any]:
    env = scrub_auth_env()
    try:
        cp = runner([claude_bin, "auth", "status"], env=env, timeout=timeout)
        combined = f"{cp.stdout or ''}\n{cp.stderr or ''}"
        status = parse_claude_auth_status(combined.strip())
        return {
            "status": status,
            "returncode": cp.returncode,
            "output": summarize_claude_output(combined.strip())[:500],
            "checked_without_api_key_env": True,
        }
    except subprocess.TimeoutExpired:
        return {"status": "unknown", "returncode": 124, "output": "timeout", "checked_without_api_key_env": True}
    except FileNotFoundError:
        return {"status": "unknown", "returncode": 127, "output": "claude command not found", "checked_without_api_key_env": True}
    except Exception as exc:
        return {"status": "unknown", "returncode": 2, "output": redact(exc), "checked_without_api_key_env": True}


def _profile_severity(
    refs: list[tuple[str, str]],
    auth: dict[str, dict[str, Any]],
    external_auth: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    severity = "ok"
    notes: list[str] = []
    required_providers = {provider for _, provider in refs}
    external = external_auth or {}

    def bump(new_severity: str) -> None:
        nonlocal severity
        if severity == "fail":
            return
        if new_severity == "fail" or severity == "ok":
            severity = new_severity

    for path, provider in refs:
        if provider in {"openrouter", "custom", "local", "api-key"}:
            continue
        if provider in {"claude-cli", "claude-code-cli"}:
            status = external.get("claude_cli", {}).get("status", "unknown")
            if status != "logged_in":
                bump("fail" if status == "logged_out" else "unknown")
                notes.append(f"{path} {provider} is {status}")
            continue
        status = auth.get(provider, {}).get("status", "unknown")
        if status != "logged_in":
            bump("fail" if status == "logged_out" else "unknown")
            notes.append(f"{path} {provider} is {status}")
    for provider, result in auth.items():
        if provider not in required_providers and result.get("status") == "logged_out" and severity == "ok":
            severity = "warn"
            notes.append(f"unused provider {provider} is logged_out")
    return severity, notes


def check_host(
    *,
    host: str | None = None,
    profile_root: Path | None = None,
    providers: Iterable[str] = DEFAULT_PROVIDERS,
    runner: Runner = run_command,
    hermes_bin: str = "hermes",
    claude_bin: str = "claude",
    check_claude: bool = True,
    include_default: bool = False,
) -> dict[str, Any]:
    host_name = host or socket.gethostname()
    root = Path(profile_root or (Path.home() / ".hermes" / "profiles"))
    provider_list = tuple(dict.fromkeys(providers))
    profiles = discover_profiles(root)
    host_notes: list[str] = []
    if include_default:
        profiles = ["default"] + profiles
    if not profiles:
        host_notes.append("no profiles found")

    entries: list[dict[str, Any]] = []
    summary = {"ok": 0, "warn": 0, "fail": 0, "unknown": 0}

    external_auth: dict[str, Any] = {}
    if check_claude:
        external_auth["claude_cli"] = check_claude_cli(runner, claude_bin=claude_bin)

    for profile in profiles:
        config = load_default_config() if profile == "default" else load_profile_config(root, profile)
        refs = configured_provider_references(config)
        auth = {
            provider: hermes_auth_status(runner, provider, hermes_bin=hermes_bin, profile=profile)
            for provider in provider_list
        }
        severity, notes = _profile_severity(refs, auth, external_auth)
        summary[severity] += 1
        entries.append(
            {
                "profile": profile,
                "severity": severity,
                "configured_provider_refs": [{"path": path, "provider": provider} for path, provider in refs],
                "auth": auth,
                "notes": notes,
            }
        )

    if not profiles:
        summary["warn"] += 1

    return {
        "schema_version": 1,
        "host": host_name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile_root": str(root),
        "providers_checked": list(provider_list),
        "profiles_checked": len(profiles),
        "profiles": entries,
        "external_auth": external_auth,
        "host_notes": host_notes,
        "summary": summary,
    }


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# Hermes auth watchdog — {report['host']}",
        "",
        f"timestamp: {report['timestamp']}",
        f"profile_root: `{report['profile_root']}`",
        "",
        "## Summary",
        "",
        f"ok={report['summary']['ok']} warn={report['summary']['warn']} fail={report['summary']['fail']} unknown={report['summary']['unknown']}",
        "",
        "## Profiles",
        "",
    ]
    for profile in report.get("profiles", []):
        notes = "; ".join(profile.get("notes") or []) or "none"
        lines.append(f"- {profile['profile']}: {profile['severity']} — {notes}")
    if not report.get("profiles"):
        lines.append("- no profiles found")
    if report.get("external_auth"):
        lines.extend(["", "## External auth", ""])
        for name, result in report["external_auth"].items():
            lines.append(f"- {name}: {result.get('status', 'unknown')}")
    lines.append("")
    return "\n".join(lines)


def exit_code_for(report: dict[str, Any]) -> int:
    if report["summary"].get("fail", 0) > 0:
        return 1
    if report["summary"].get("unknown", 0) > 0:
        return 2
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only fleet Hermes auth watchdog")
    parser.add_argument("--profile-root", type=Path, default=Path.home() / ".hermes" / "profiles")
    parser.add_argument("--provider", action="append", dest="providers", help="Provider to check; repeatable")
    parser.add_argument("--host", default=None)
    parser.add_argument("--hermes-bin", default="hermes")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--no-claude", action="store_true")
    parser.add_argument("--include-default", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--print", action="store_true", help="Print JSON to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = check_host(
        host=args.host,
        profile_root=args.profile_root,
        providers=tuple(args.providers or DEFAULT_PROVIDERS),
        hermes_bin=args.hermes_bin,
        claude_bin=args.claude_bin,
        check_claude=not args.no_claude,
        include_default=args.include_default,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(markdown_summary(report), encoding="utf-8")
    if args.print or not (args.output_json or args.output_md):
        print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code_for(report)


if __name__ == "__main__":
    raise SystemExit(main())
