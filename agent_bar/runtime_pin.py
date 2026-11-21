"""runtime_pin.py — confere se o agent-runtime instalado e o commit fixado no pyproject.

`pip install -e .[gui]` NAO reinstala o runtime quando so o SHA fixado muda (a versao continua 0.1.0): a barra rodaria calada com
o codigo antigo do modulo `approval`. Aqui se le o SHA fixado do pyproject e o instalado do `direct_url.json` que o pip grava
(`vcs_info.commit_id`). Divergente => aviso na tela e no log, com o comando exato. Origem desconhecida => dito, nunca assumido ok.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
_PIN = re.compile(r"agent-runtime\s*@\s*(git\+https://github\.com/Plan2Space/agent-runtime)@([0-9a-f]{40})")
LOG = logging.getLogger("agent_bar")


@dataclass(frozen=True)
class PinStatus:
    ok: bool | None                  # True confere · False diverge · None nao deu para saber
    pinned: str | None
    installed: str | None
    message: str
    command: str = ""


def pinned_sha(pyproject: Path) -> str | None:
    try:
        m = _PIN.search(pyproject.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(2) if m else None


def _read_direct_url() -> str | None:
    from importlib import metadata
    return metadata.distribution("agent-runtime").read_text("direct_url.json")


def check(pyproject: Path = PYPROJECT, read_direct_url: Callable[[], str | None] = _read_direct_url) -> PinStatus:
    pinned = pinned_sha(pyproject)
    if pinned is None:
        return PinStatus(None, None, None, f"SHA do agent-runtime não encontrado em {pyproject.name}: conferência não feita")
    command = f'.venv\\Scripts\\python -m pip install --force-reinstall --no-deps "agent-runtime @ git+https://github.com/Plan2Space/agent-runtime@{pinned}"'
    try:
        raw = read_direct_url()
    except Exception as exc:                                                  # noqa: BLE001 - pacote ausente, metadado ilegivel
        return PinStatus(None, pinned, None, f"agent-runtime instalado ilegível ({type(exc).__name__}: {exc})", command)
    try:
        installed = ((json.loads(raw) or {}).get("vcs_info") or {}).get("commit_id") if raw else None
    except ValueError:
        installed = None
    if not installed:
        return PinStatus(None, pinned, None, "agent-runtime instalado sem commit de origem (não veio do git): conferência não feita",
                         command)
    if installed != pinned:
        return PinStatus(False, pinned, installed,
                         f"agent-runtime desatualizado: instalado {installed[:12]}, fixado no pyproject {pinned[:12]}. "
                         f"A barra está usando as regras antigas do runtime. Atualize com: {command}", command)
    return PinStatus(True, pinned, installed, f"agent-runtime {installed[:12]} confere com o pyproject")


def log_status(st: PinStatus) -> None:
    (LOG.info if st.ok else LOG.warning)("runtime: %s", st.message)
