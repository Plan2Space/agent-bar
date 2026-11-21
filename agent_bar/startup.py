"""startup.py — log em arquivo e erro fatal visivel. Sem Qt (o erro pode ser justamente o Qt nao carregar).

Com `pythonw.exe` nao existe console: sem isto, uma falha antes da UI subir sumiria calada.
Log: %LOCALAPPDATA%\\agent_bar\\logs\\agent_bar.log (ou AGENT_BAR_LOG_DIR), fora do repo, com rotacao.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from collections.abc import Callable, Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER = "agent_bar"


def log_dir(env: Mapping[str, str] | None = None) -> Path:
    e = os.environ if env is None else env
    if e.get("AGENT_BAR_LOG_DIR"):
        return Path(e["AGENT_BAR_LOG_DIR"])
    base = e.get("LOCALAPPDATA") or e.get("APPDATA") or str(Path.home())
    return Path(base) / "agent_bar" / "logs"


def configure_logging(directory: Path | None = None) -> Path:
    d = directory or log_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "agent_bar.log"
    log = logging.getLogger(LOGGER)
    for h in list(log.handlers):                                             # reconfigurar (testes) nao duplica handlers
        log.removeHandler(h)
        h.close()
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return path


_LOCAL_KEYS = ("AGENT_BAR_", "EVENT_STORE_DB_PATH")


def load_local_env(path: Path, env: dict[str, str] | None = None) -> list[str]:
    """Le `launchers/local.env` (KEY=VALUE, fora do git): so fontes da barra (AGENT_BAR_*, EVENT_STORE_DB_PATH),
    e nunca sobrescreve o que ja esta no ambiente. Devolve as chaves aplicadas (para o log)."""
    target = os.environ if env is None else env
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()        # utf-8-sig: arquivo salvo com BOM pelo Bloco de Notas
    except OSError:
        return []
    applied = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key.startswith(_LOCAL_KEYS) and key not in target:
            target[key] = value
            applied.append(key)
    return applied


def message_box(title: str, text: str) -> None:
    """Caixa de mensagem nativa (Win32), sem depender do Qt. Fora do Windows, stderr."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)            # MB_ICONERROR
    except (AttributeError, OSError):
        print(f"{title}: {text}", file=sys.stderr)


def report_fatal(exc: BaseException, log_path: Path | None, show: Callable[[str, str], None] = message_box) -> None:
    logging.getLogger(LOGGER).error("falha ao iniciar a barra\n%s", "".join(traceback.format_exception(exc)))
    for h in logging.getLogger(LOGGER).handlers:
        h.flush()
    where = f"\n\nDetalhes no log:\n{log_path}" if log_path else ""
    show("Plan2Space Agent Bar", f"A barra nao conseguiu iniciar.\n\n{type(exc).__name__}: {exc}{where}")
