"""health.py — verificacao de saude sem tarefa perigosa: so `--version`, status de login e `GET /api/version`.

Nenhum prompt, nenhum modelo carregado, nenhuma escrita. O processo roda com ambiente isolado (`isolated_env`), sem
janela e com tempo limite. A saida bruta NUNCA sai daqui: o status de login traz e-mail e organizacao, entao so se
extrai o veredito (AUTHENTICATED / AUTH_REQUIRED / UNKNOWN / ERROR) e uma frase curta fixa.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_bar.cockpit.acp import creation_flags, isolated_env

VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?(?:[-+.][0-9A-Za-z.]+)?")
TIMEOUT_S = 15.0

# comando de status de login, so leitura, por provedor; o parser recebe (exit_code, stdout) e devolve o veredito
AUTH_COMMANDS: dict[str, tuple[Sequence[str], Callable[[int, str], str]]] = {}


@dataclass(frozen=True)
class Health:
    provider_id: str
    version: str | None
    auth_status: str
    connection_status: str                                                   # DISCONNECTED (pronto p/ conectar) | NOT_FOUND | ERROR
    detail: str                                                              # frase curta fixa, sem saida bruta
    checked_at: str


Runner = Callable[..., subprocess.CompletedProcess]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _run(argv: Sequence[str], run: Runner) -> tuple[int, str] | None:
    try:
        cp = run(list(argv), capture_output=True, timeout=TIMEOUT_S, env=isolated_env(), stdin=subprocess.DEVNULL,
                 creationflags=creation_flags())
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = []                                                               # o codex escreve o status de login no stderr
    for out in (cp.stdout, cp.stderr):
        out = out or b""
        parts.append(out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out))
    return cp.returncode, "\n".join(p for p in parts if p)


def parse_version(text: str) -> str | None:
    m = VERSION_RE.search(text or "")
    return m.group(0) if m else None


def claude_auth(code: int, out: str) -> str:
    try:
        data = json.loads(out)
    except ValueError:
        return "UNKNOWN"
    if not isinstance(data, dict) or "loggedIn" not in data:
        return "UNKNOWN"
    return "AUTHENTICATED" if data["loggedIn"] is True else "AUTH_REQUIRED"


def codex_auth(code: int, out: str) -> str:
    low = out.lower()
    if "not logged in" in low:
        return "AUTH_REQUIRED"
    if code == 0 and "logged in" in low:
        return "AUTHENTICATED"
    return "UNKNOWN"


AUTH_COMMANDS.update({
    "claude-cli": (("auth", "status"), claude_auth),
    "codex-cli": (("login", "status"), codex_auth),
})
# o adaptador ACP do Claude usa o mesmo login do Claude Code: o status vem do `claude`, nao do adaptador
AUTH_VIA = {"claude-acp": ("claude-cli", "claude"), "codex-acp": ("codex-cli", "codex")}


def check_process(provider: dict, run: Runner = subprocess.run, which: Callable[[str], str | None] = shutil.which) -> Health:
    pid = provider["provider_id"]
    exe = provider.get("executable_path")
    if not exe:
        return Health(pid, None, "UNKNOWN", "NOT_FOUND", "executavel nao encontrado no PATH", _now())
    got = _run([exe, "--version"], run)
    if got is None:
        return Health(pid, None, "UNKNOWN", "ERROR", "--version nao respondeu", _now())
    code, out = got
    version = parse_version(out) if code == 0 else None
    if version is None:
        return Health(pid, None, "UNKNOWN", "ERROR", f"--version saiu com codigo {code}", _now())
    auth_id, auth_name = AUTH_VIA.get(pid, (pid, None))
    auth = "UNKNOWN"
    detail = "versao lida; login nao verificavel por este adaptador"
    if auth_id in AUTH_COMMANDS:
        args, parser = AUTH_COMMANDS[auth_id]
        auth_exe = exe if auth_name is None else which(auth_name)
        if auth_exe:
            res = _run([auth_exe, *args], run)
            auth = "ERROR" if res is None else parser(*res)
            detail = {"AUTHENTICATED": "logado (mecanismo oficial do CLI)", "AUTH_REQUIRED": "login necessario no CLI",
                      "ERROR": "status de login nao respondeu"}.get(auth, "status de login inconclusivo")
    return Health(pid, version, auth, "DISCONNECTED", detail, _now())


def check_http(provider: dict, urlopen: Callable = urllib.request.urlopen, timeout: float = 3.0) -> Health:
    pid, endpoint = provider["provider_id"], provider.get("endpoint")
    if not endpoint:
        return Health(pid, None, "UNKNOWN", "ERROR", "sem endpoint", _now())
    try:
        with urlopen(f"{endpoint}/api/version", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return Health(pid, None, "UNSUPPORTED", "ERROR", "servidor local nao respondeu", _now())
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str):
        return Health(pid, None, "UNSUPPORTED", "ERROR", "resposta sem versao", _now())
    return Health(pid, version, "UNSUPPORTED", "DISCONNECTED", "servidor local no ar (sem autenticacao)", _now())


def check(provider: dict, **kw) -> Health:
    if provider["provider_type"] in ("ACP", "CLI"):
        return check_process(provider, **{k: v for k, v in kw.items() if k in ("run", "which")})
    return check_http(provider, **{k: v for k, v in kw.items() if k in ("urlopen", "timeout")})


def apply(provider: dict, health: Health) -> dict:
    """Provider v1 atualizado com o resultado do health (versao, auth, conexao, quando)."""
    return {**provider, "version": health.version, "auth_status": health.auth_status,
            "connection_status": health.connection_status, "last_verified_at": health.checked_at}
