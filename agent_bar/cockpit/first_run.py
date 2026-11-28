"""first_run.py — a lista de verificação do primeiro uso (FR-01). Sem Qt.

O P2S Studio depende de programas que a tela nunca checava nem explicava: o Claude (instalado e com login), o adaptador
`claude-agent-acp` do npm, o GitHub CLI com login, o Ollama (opcional), uma pasta de projeto e, só para o Design, o
motor de renders com o SketchUp (opcional). Cada item vira um `Check` com estado, uma frase simples e UMA ação.

A página abre na 1ª execução (sem `first_run_done` no settings.json) e sempre que faltar algo essencial. Item opcional
ausente nunca bloqueia "Começar". As consultas à máquina entram por `Env` (o teste troca por falsos).
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

OK, MISSING, OPTIONAL = "ok", "missing", "optional"
ADAPTER_CMD = "npm install -g @agentclientprotocol/claude-agent-acp"
CLAUDE_INSTALL_URL = "https://claude.com/claude-code"
CLAUDE_LOGIN_CMD = "claude"
GH_INSTALL_URL = "https://cli.github.com/"
GH_LOGIN_CMD = "gh auth login"
OLLAMA_URL = "https://ollama.com/download"


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    state: str                                  # ok | missing | optional (ausente, mas não bloqueia)
    sentence: str
    action: tuple[str, str] | None = None       # ("copy", comando) | ("url", endereço) | ("folder", "")
    essential: bool = True

    @property
    def button(self) -> str:
        if self.action is None:
            return ""
        return {"copy": "Copiar comando", "url": "Abrir página de instalação", "folder": "Escolher pasta…"}[self.action[0]]


def _gh_found(which: Callable[[str], str | None]) -> bool:
    from agent_bar.cockpit.github import _default_exe
    exe = _default_exe()[0]
    return bool(which("gh") or which(exe) or os.path.isfile(exe))


def _default_folders() -> list[Path]:
    from agent_bar.cockpit import profiles
    from agent_bar.cockpit import project_folders as PF
    from agent_bar.cockpit.store import data_dir
    return [*PF.load(data_dir() / "preferences.json"), *profiles.workspaces()]


def _default_engine() -> tuple[bool, str]:
    from agent_bar.platform.sketchup_sample import preflight_from_env
    return preflight_from_env()


def _default_claude_login() -> bool | None:
    from agent_bar.collectors import claude_login_state
    return claude_login_state()


@dataclass
class Env:
    """O que a lista consulta na máquina. Padrões reais; o teste passa falsos."""
    which: Callable[[str], str | None] = shutil.which
    claude_login: Callable[[], bool | None] = _default_claude_login
    gh_login: Callable[[], bool | None] = lambda: None                      # None = não deu para saber (não bloqueia)
    folders: Callable[[], list[Path]] = _default_folders
    engine: Callable[[], tuple[bool, str]] = _default_engine
    extra: dict = field(default_factory=dict)


def collect(env: Env | None = None) -> list[Check]:
    e = env or Env()
    out: list[Check] = []
    if not e.which("claude"):
        out.append(Check("claude", "Claude", MISSING, "Instale o Claude Code: é ele que conversa e edita os arquivos.",
                         ("url", CLAUDE_INSTALL_URL)))
    elif e.claude_login() is False:
        out.append(Check("claude", "Claude", MISSING, "Entre na sua conta do Claude: abra o PowerShell, rode o comando "
                         "copiado e siga as instruções.", ("copy", CLAUDE_LOGIN_CMD)))
    else:
        out.append(Check("claude", "Claude", OK, "Claude instalado."))
    if e.which("claude-agent-acp"):
        out.append(Check("adapter", "Ligação do Claude com o Studio", OK, "Instalada."))
    else:
        out.append(Check("adapter", "Ligação do Claude com o Studio", MISSING,
                         "Falta um complemento que liga o Claude ao Studio. Rode o comando copiado no PowerShell.",
                         ("copy", ADAPTER_CMD)))
    if not _gh_found(e.which):
        out.append(Check("github", "GitHub", MISSING, "Instale o GitHub CLI para ver os PRs.", ("url", GH_INSTALL_URL),
                         essential=False))
    elif e.gh_login() is False:
        out.append(Check("github", "GitHub", MISSING, "Entre na sua conta do GitHub: rode o comando copiado no "
                         "PowerShell e siga as instruções.", ("copy", GH_LOGIN_CMD), essential=False))
    else:
        out.append(Check("github", "GitHub", OK, "GitHub CLI instalado.", essential=False))
    if e.which("ollama") or os.environ.get("OLLAMA_HOST", "").strip():
        out.append(Check("ollama", "Modelo local (Ollama)", OK, "Instalado.", essential=False))
    else:
        out.append(Check("ollama", "Modelo local (Ollama)", OPTIONAL, "Opcional: para conversar com um modelo que roda "
                         "no seu computador.", ("url", OLLAMA_URL), essential=False))
    if e.folders():
        out.append(Check("folder", "Pasta do projeto", OK, "Escolhida."))
    else:
        out.append(Check("folder", "Pasta do projeto", MISSING, "Escolha a pasta do seu projeto: o agente trabalha "
                         "dentro dela.", ("folder", "")))
    ready, _why = e.engine()
    out.append(Check("engine", "Renders do Design (SketchUp 2026)", OK if ready else OPTIONAL,
                     "Pronto." if ready else "Opcional: só para gerar renders na aba Design.", essential=False))
    return out


def missing_essential(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.essential and c.state == MISSING]


def should_open(checks: list[Check], done: bool) -> bool:
    """Abre na 1ª execução e sempre que faltar algo essencial."""
    return not done or bool(missing_essential(checks))


def needed_now() -> bool:
    """Para o app: abrir o primeiro uso agora? (lê a máquina e o settings.json)"""
    from agent_bar import settings as S
    return should_open(collect(), S.load().first_run_done)


def mark_done() -> None:
    from agent_bar import settings as S
    s = S.load()
    s.first_run_done = True
    S.save(s)


__all__ = ["MISSING", "OK", "OPTIONAL", "Check", "Env", "collect", "mark_done", "missing_essential", "needed_now", "should_open"]
