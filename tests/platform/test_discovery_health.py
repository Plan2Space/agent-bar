"""Descoberta (sem instalar nem executar) e health (so --version / status de login / GET /api/version)."""
from __future__ import annotations

import io
import json
import subprocess

import pytest

from agent_bar.platform import health as h
from agent_bar.platform.contracts import validate_provider
from agent_bar.platform.discovery import discover

FOUND = {"claude-agent-acp": "C:\\npm\\claude-agent-acp.cmd", "claude": "C:\\npm\\claude.cmd", "codex": "C:\\npm\\codex.cmd"}


def _by_id(providers):
    return {p["provider_id"]: p for p in providers}


def test_discovery_lists_found_and_missing_providers_without_running_anything():
    calls = []

    def which(name):
        calls.append(name)
        return FOUND.get(name)
    got = _by_id(discover(which=which, env={}))
    assert set(got) == {"claude-acp", "claude-cli", "codex-cli", "codex-acp", "ollama"}
    assert got["claude-acp"]["executable_path"] == FOUND["claude-agent-acp"]
    assert got["claude-acp"]["connection_status"] == "DISCONNECTED"
    assert got["codex-acp"]["connection_status"] == "NOT_FOUND" and "nao encontrado" in got["codex-acp"]["discovery_source"]
    assert got["ollama"]["endpoint"] == "http://127.0.0.1:11434" and got["ollama"]["cost_class"] == "local"
    assert got["claude-cli"]["cost_class"] == "subscription_cli"
    assert sorted(calls) == sorted(["claude-agent-acp", "claude", "codex", "codex-acp"])
    for p in got.values():
        validate_provider(p)
        assert p["version"] is None and p["auth_status"] == "UNKNOWN" and p["last_verified_at"] is None


@pytest.mark.parametrize("raw,expected", [
    ("127.0.0.1:11500", "http://127.0.0.1:11500"),
    ("0.0.0.0", "http://127.0.0.1"),                                         # bind de servidor vira loopback para conectar
    ("http://box:11434/", "http://box:11434"),
])
def test_ollama_host_env_changes_the_endpoint(raw, expected):
    got = _by_id(discover(which=lambda n: None, env={"OLLAMA_HOST": raw}))["ollama"]
    assert got["endpoint"] == expected and got["discovery_source"] == "config (OLLAMA_HOST)"


# ------------------------------------------------------------------------------------------------ health
class FakeRun:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        key = " ".join(argv[1:])
        out = self.outputs[key]
        if isinstance(out, Exception):
            raise out
        code, text, *err = out
        return subprocess.CompletedProcess(argv, code, text.encode("utf-8"), (err[0] if err else "").encode("utf-8"))


def _provider(pid):
    return _by_id(discover(which=lambda n: FOUND.get(n), env={}))[pid]


CLAUDE_STATUS = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "pessoa@example.com", "orgName": "Org"})


def test_claude_health_reads_version_and_login_without_keeping_the_raw_output():
    run = FakeRun({"--version": (0, "2.1.158 (Claude Code)\n"), "auth status": (0, CLAUDE_STATUS)})
    got = h.check(_provider("claude-cli"), run=run)
    assert (got.version, got.auth_status, got.connection_status) == ("2.1.158", "AUTHENTICATED", "DISCONNECTED")
    assert "example.com" not in repr(got) and "Org" not in repr(got)
    for _, kw in run.calls:
        assert kw["timeout"] <= 30 and kw["stdin"] == subprocess.DEVNULL
        assert not any(k.upper().startswith(("ANTHROPIC_", "GH_")) for k in kw["env"])


def test_the_acp_adapter_takes_the_login_status_from_the_claude_cli():
    run = FakeRun({"--version": (0, "0.109.1\n"), "auth status": (0, json.dumps({"loggedIn": False}))})
    got = h.check(_provider("claude-acp"), run=run, which=lambda n: FOUND.get(n))
    assert got.version == "0.109.1" and got.auth_status == "AUTH_REQUIRED"
    assert run.calls[1][0][0] == FOUND["claude"]


@pytest.mark.parametrize("code,text,expected", [
    (0, "Logged in using ChatGPT", "AUTHENTICATED"),
    (1, "Not logged in", "AUTH_REQUIRED"),
    (2, "???", "UNKNOWN"),
])
def test_codex_login_status_is_classified(code, text, expected):
    run = FakeRun({"--version": (0, "codex-cli 0.164.0"), "login status": (code, text)})
    got = h.check(_provider("codex-cli"), run=run)
    assert got.version == "0.164.0" and got.auth_status == expected


def test_codex_login_status_on_stderr_is_read_too():
    run = FakeRun({"--version": (0, "codex-cli 0.164.0"), "login status": (0, "", "Logged in using ChatGPT")})
    assert h.check(_provider("codex-cli"), run=run).auth_status == "AUTHENTICATED"


def test_missing_executable_timeout_and_bad_version_are_explicit_not_hidden():
    assert h.check(_provider("codex-acp")).connection_status == "NOT_FOUND"
    hung = FakeRun({"--version": subprocess.TimeoutExpired("claude", 15)})
    assert h.check(_provider("claude-cli"), run=hung).connection_status == "ERROR"
    broken = FakeRun({"--version": (1, "boom")})
    got = h.check(_provider("claude-cli"), run=broken)
    assert got.connection_status == "ERROR" and "codigo 1" in got.detail


def _urlopen(payload=None, exc=None):
    def opener(url, timeout):
        opener.url = url
        if exc:
            raise exc
        return io.BytesIO(json.dumps(payload).encode())
    return opener


def test_ollama_health_uses_only_the_version_endpoint():
    op = _urlopen({"version": "0.40.16"})
    got = h.check(_provider("ollama"), urlopen=op)
    assert op.url == "http://127.0.0.1:11434/api/version"
    assert (got.version, got.auth_status, got.connection_status) == ("0.40.16", "UNSUPPORTED", "DISCONNECTED")


def test_ollama_down_is_an_error_with_a_reason():
    import urllib.error
    got = h.check(_provider("ollama"), urlopen=_urlopen(exc=urllib.error.URLError("recusado")))
    assert got.connection_status == "ERROR" and got.version is None and "nao respondeu" in got.detail


def test_apply_writes_the_health_into_a_valid_provider_record():
    run = FakeRun({"--version": (0, "2.1.158"), "auth status": (0, CLAUDE_STATUS)})
    p = _provider("claude-cli")
    rec = h.apply(p, h.check(p, run=run))
    validate_provider(rec)
    assert rec["version"] == "2.1.158" and rec["last_verified_at"]


@pytest.mark.real_agent
def test_real_machine_discovery_and_health():
    """Opt-in (`-m real_agent`): descoberta + health na maquina real. Nao imprime saida bruta de login."""
    for p in discover():
        got = h.check(p)
        print(p["provider_id"], got.connection_status, got.version, got.auth_status, got.detail)
