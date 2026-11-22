"""discovery.py — descobre provedores na maquina SEM instalar nem executar nada.

Executavel: `shutil.which` (mesma resolucao do `where.exe`/`Get-Command`, com PATHEXT: acha o shim `.cmd` do npm).
HTTP local: so o endpoint configurado (OLLAMA_HOST ou o padrao `127.0.0.1:11434`); quem toca a rede e o health check.
Ausente nao some da lista: aparece como `NOT_FOUND`, com o motivo, para a UI explicar em vez de esconder.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agent_bar.platform.contracts import all_capabilities, validate_provider

OLLAMA_DEFAULT = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class Candidate:
    provider_id: str
    display_name: str
    provider_type: str
    adapter_id: str
    cost_class: str
    executable: str | None = None                                            # nome procurado no PATH
    endpoint_env: str | None = None                                          # variavel que muda o endpoint
    endpoint_default: str | None = None


CANDIDATES: tuple[Candidate, ...] = (
    Candidate("claude-acp", "Claude Code (ACP)", "ACP", "acp", "subscription_cli", executable="claude-agent-acp"),
    Candidate("claude-cli", "Claude Code (CLI)", "CLI", "claude-cli", "subscription_cli", executable="claude"),
    Candidate("codex-cli", "Codex (CLI)", "CLI", "codex-cli", "subscription_cli", executable="codex"),
    Candidate("codex-acp", "Codex (ACP)", "ACP", "acp", "subscription_cli", executable="codex-acp"),
    Candidate("ollama", "Ollama (local)", "LOCAL_HTTP", "ollama", "local",
              endpoint_env="OLLAMA_HOST", endpoint_default=OLLAMA_DEFAULT),
)


def _endpoint(c: Candidate, env: Mapping[str, str]) -> str:
    raw = (env.get(c.endpoint_env or "") or "").strip() or (c.endpoint_default or "")
    if raw and "://" not in raw:
        raw = "http://" + raw
    host = raw.split("://", 1)[1]
    if host.startswith("0.0.0.0"):                                           # endereco de bind do servidor, nao de conexao
        raw = raw.replace("0.0.0.0", "127.0.0.1", 1)
    return raw.rstrip("/")


def discover(which: Callable[[str], str | None] = shutil.which, env: Mapping[str, str] | None = None,
             candidates: tuple[Candidate, ...] = CANDIDATES) -> list[dict]:
    """Provider v1 por candidato. Nada verificado ainda: versao/auth UNKNOWN e capacidades UNKNOWN."""
    env = os.environ if env is None else env
    out = []
    for c in candidates:
        path = which(c.executable) if c.executable else None
        if c.executable:
            status = "DISCONNECTED" if path else "NOT_FOUND"
            source = f"PATH ({c.executable})" if path else f"PATH ({c.executable}): nao encontrado"
            endpoint = None
        else:
            status, endpoint = "DISCONNECTED", _endpoint(c, env)
            source = f"config ({c.endpoint_env})" if env.get(c.endpoint_env or "") else "padrao do provedor"
        out.append(validate_provider({
            "schema": "p2s.provider.v1",
            "provider_id": c.provider_id,
            "display_name": c.display_name,
            "provider_type": c.provider_type,
            "adapter_id": c.adapter_id,
            "executable_path": path,
            "endpoint": endpoint,
            "version": None,
            "auth_status": "UNKNOWN",
            "connection_status": status,
            "capabilities": all_capabilities(method="discovery: nao verificado"),
            "discovery_source": source,
            "last_verified_at": None,
            "cost_class": c.cost_class,
        }))
    return out
