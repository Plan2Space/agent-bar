"""acp_mcp.py — servidores MCP que o Chat declara ao agente ACP no `session/new` (UX-12). Sem Qt.

Configuração SÓ do app: `acp_mcp.json` na pasta de dados do Cockpit, no formato do ACP
(`[{"name", "command", "args", "env": [{"name", "value"}]}]`). Sem o arquivo, nada é declarado (`mcpServers: []`) e a UI
diz "MCP indisponível". Nunca escreve no `.mcp.json` do workspace: `from_claude_entry` só converte uma entrada lida.
Variável de ambiente que parece credencial (ANTHROPIC_, GH_, *TOKEN*, *KEY*...) é recusada: o servidor MCP não herda segredo.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agent_bar.cockpit.acp import _denied

CONFIG_NAME = "acp_mcp.json"


@dataclass(frozen=True)
class McpConfig:
    servers: list[dict] = field(default_factory=list)
    source: str = ""
    error: str = ""                     # arquivo existe mas foi recusado (o motivo vai para a UI)

    def names(self) -> list[str]:
        return [str(s["name"]) for s in self.servers]

    def status_text(self) -> str:
        if self.error:
            return f"MCP indisponível: {self.error}"
        if not self.servers:
            return f"MCP indisponível (sem {CONFIG_NAME})"
        return "MCP declarado: " + ", ".join(self.names())


def _check(server: object, i: int) -> dict:
    if not isinstance(server, dict):
        raise ValueError(f"servidor {i}: não é objeto")
    name, command = server.get("name"), server.get("command")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"servidor {i}: sem 'name'")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"{name}: sem 'command'")
    args = server.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError(f"{name}: 'args' precisa ser lista de texto")
    env = server.get("env", [])
    if not isinstance(env, list):
        raise ValueError(f"{name}: 'env' precisa ser lista de {{name, value}}")
    out_env = []
    for e in env:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not isinstance(e.get("value"), str):
            raise ValueError(f"{name}: item de 'env' sem name/value de texto")
        if _denied(e["name"]):
            raise ValueError(f"{name}: variável {e['name']} recusada (credencial não vai para servidor MCP)")
        out_env.append({"name": e["name"], "value": e["value"]})
    return {"name": name, "command": command, "args": list(args), "env": out_env}


def load(path: str | Path) -> McpConfig:
    p = Path(path)
    if not p.is_file():
        return McpConfig(source=str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise ValueError("o arquivo precisa ser uma lista de servidores")
        servers = [_check(s, i) for i, s in enumerate(data)]
        if len({s["name"] for s in servers}) != len(servers):
            raise ValueError("nomes de servidor repetidos")
    except (OSError, ValueError) as exc:                                     # JSONDecodeError é ValueError
        return McpConfig(source=str(p), error=str(exc)[:200])
    return McpConfig(servers=servers, source=str(p))


def from_claude_entry(name: str, entry: Mapping[str, object]) -> tuple[dict, list[str]]:
    """Converte uma entrada stdio do `.mcp.json` (formato Claude Code) para o formato ACP. Devolve (servidor, avisos).
    O ACP não tem `cwd` por servidor: o aviso diz isso em vez de descartar calado."""
    notes = []
    if entry.get("cwd"):
        notes.append(f"{name}: 'cwd' ({entry['cwd']}) não existe no ACP; o servidor depende do 'env' (ex.: PYTHONPATH)")
    env = entry.get("env") or {}
    server = {"name": name, "command": str(entry.get("command", "")), "args": [str(a) for a in entry.get("args") or []],
              "env": [{"name": str(k), "value": str(v)} for k, v in dict(env).items()]}  # type: ignore[arg-type]
    return _check(server, 0), notes


__all__ = ["CONFIG_NAME", "McpConfig", "from_claude_entry", "load"]
