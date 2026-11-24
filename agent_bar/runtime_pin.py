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
UV_LOCK = PYPROJECT.with_name("uv.lock")
_PIN = re.compile(r"agent-runtime\s*@\s*(git\+https://github\.com/Plan2Space/agent-runtime)@([0-9a-f]{40})")
# uv.lock: source = { git = "https://github.com/Plan2Space/agent-runtime?rev=<sha>#<sha>" } no pacote agent-runtime
_LOCK = re.compile(r'name = "agent-runtime"\s*\nversion = "[^"]*"\s*\nsource = \{ git = "[^"#]*#([0-9a-f]{40})"')
UV_SYNC = "uv sync --frozen --extra gui"
LOG = logging.getLogger("agent_bar")


@dataclass(frozen=True)
class PinStatus:
    ok: bool | None                  # True confere · False diverge · None nao deu para saber
    pinned: str | None
    installed: str | None
    message: str
    command: str = ""
    locked: str | None = None        # o SHA que o uv.lock fixa (None = sem uv.lock ou sem o pacote nele)


def pinned_sha(pyproject: Path) -> str | None:
    try:
        m = _PIN.search(pyproject.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(2) if m else None


def locked_sha(uv_lock: Path) -> str | None:
    try:
        m = _LOCK.search(uv_lock.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _read_direct_url() -> str | None:
    from importlib import metadata
    return metadata.distribution("agent-runtime").read_text("direct_url.json")


def check(pyproject: Path = PYPROJECT, read_direct_url: Callable[[], str | None] = _read_direct_url,
          uv_lock: Path | None = None) -> PinStatus:
    """Instalado (direct_url.json do .venv que roda o app) x fixado no pyproject x fixado no uv.lock. Qualquer par que
    divirja é `ok=False` com os SHAs e o comando reproduzível; não reinstala nada sozinho."""
    pinned = pinned_sha(pyproject)
    lock_path = uv_lock if uv_lock is not None else pyproject.with_name("uv.lock")
    locked = locked_sha(lock_path)
    if pinned is None:
        return PinStatus(None, None, None, f"SHA do agent-runtime não encontrado em {pyproject.name}: conferência não feita",
                         locked=locked)
    if locked is not None and locked != pinned:
        return PinStatus(False, pinned, None,
                         f"pyproject e uv.lock divergem no agent-runtime: pyproject {pinned[:12]}, uv.lock {locked[:12]}. "
                         "Regenere o lock com `uv lock` no checkout e reinstale com "
                         f"`{UV_SYNC}`.", UV_SYNC, locked)
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
    lock_txt = f", uv.lock {locked[:12]}" if locked else ", sem uv.lock"
    if installed != pinned:
        return PinStatus(False, pinned, installed,
                         f"agent-runtime desatualizado: instalado {installed[:12]}, fixado no pyproject {pinned[:12]}{lock_txt}. "
                         f"O app está usando as regras antigas do runtime. Atualize com: {command}"
                         + (f" (ou `{UV_SYNC}`)" if locked else ""), command, locked)
    return PinStatus(True, pinned, installed,
                     f"agent-runtime {installed[:12]} confere com o pyproject" + (" e o uv.lock" if locked else ""),
                     command, locked)


def log_status(st: PinStatus) -> None:
    (LOG.info if st.ok else LOG.warning)("runtime: %s", st.message)
