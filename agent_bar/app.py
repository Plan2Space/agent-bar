"""app.py — entrada da barra: `python -m agent_bar.app [--once-json] [--screenshot ARQ.png [--full]]`.

Com `pythonw.exe` (atalho da area de trabalho) nao ha console: o inicio vai para o log em arquivo
(%LOCALAPPDATA%\\agent_bar\\logs) e uma falha antes da UI aparece numa caixa de mensagem.
Uma barra por usuario: abrir de novo traz a existente para a frente.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "launchers" / "agent_bar.ico"
LOCAL_ENV = ROOT / "launchers" / "local.env"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent_bar")
    ap.add_argument("--once-json", action="store_true", help="imprime o diagnostico da coleta e sai (sem GUI)")
    ap.add_argument("--screenshot", help="grava um PNG da barra (e do painel expandido) apos a 1a coleta e sai")
    ap.add_argument("--open-panel", action="store_true", help="abre o painel expandido ao iniciar (evidencia/teste)")
    ap.add_argument("--full", action="store_true", help="com --screenshot: grava tambem a Central completa (ARQ_full.png)")
    ns = ap.parse_args(argv)
    from agent_bar import startup

    if ns.once_json:
        startup.load_local_env(LOCAL_ENV)
        from agent_bar import collectors as col
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:                                           # redirecionado no Windows sai em cp1252 sem isto (S00-07 D7)
            reconfigure(encoding="utf-8")
        print(col.collect_snapshot(claude_login=col.claude_login_state, codex_login=col.codex_login_state).diagnostic())
        return 0
    log_path = None
    try:
        log_path = startup.configure_logging()
        log = logging.getLogger(startup.LOGGER)
        applied = startup.load_local_env(LOCAL_ENV)
        log.info("iniciando pid=%s exe=%s cwd=%s repo=%s local.env=%s", os.getpid(), sys.executable, os.getcwd(), ROOT, applied or "nenhum")
        return _run_gui(ns, log)
    except Exception as exc:                                                  # noqa: BLE001 - qualquer falha antes/fora da UI tem de aparecer
        startup.report_fatal(exc, log_path)
        return 1


def _run_gui(ns: argparse.Namespace, log: logging.Logger) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from agent_bar import settings as cfg
    from agent_bar.window import Bar
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Plan2Space Agent Bar")
    if ICON.is_file():
        app.setWindowIcon(QIcon(str(ICON)))
    instance = None
    if not ns.screenshot:                                                     # captura de evidencia roda ao lado da barra aberta
        from agent_bar.single_instance import SingleInstance
        from agent_bar.startup import log_dir
        holder: dict = {}
        instance = SingleInstance(lock_dir=log_dir().parent)
        if not instance.acquire(on_activate=lambda: _bring_forward(holder.get("bar"), log)):
            log.info("ja existe uma barra aberta: pedido para vir para a frente enviado; esta instancia sai")
            return 0
        app.aboutToQuit.connect(instance.release)
    bar = Bar(cfg.load())
    if instance is not None:
        holder["bar"] = bar
    bar.show()
    log.info("barra visivel em %s,%s (%sx%s)", bar.x(), bar.y(), bar.width(), bar.height())
    if ns.open_panel:
        QTimer.singleShot(4000, bar.toggle_details)
    if ns.screenshot:
        def shot() -> None:
            bar.details.show()
            bar._sync_details()                                               # noqa: SLF001
            def save() -> None:
                bar.grab().save(ns.screenshot)
                bar.details.grab().save(ns.screenshot.replace(".png", "_panel.png"))
                if ns.full:                                                   # Central completa: execucoes do motor, benchmark, EventStore
                    bar.details.toggle_full_mode()
                    QTimer.singleShot(400, lambda: (bar.details.grab().save(ns.screenshot.replace(".png", "_full.png")),
                                                    bar.details.operations.grab().save(ns.screenshot.replace(".png", "_ops.png")),   # cards abaixo da dobra
                                                    bar.close_app()))
                else:
                    bar.close_app()
            QTimer.singleShot(400, save)
        QTimer.singleShot(7000, shot)
    return app.exec()


def _bring_forward(bar, log: logging.Logger) -> None:
    """Segunda abertura: a barra existente aparece (sai de minimizada/escondida) e o painel abre. Nada e iniciado ou parado."""
    if bar is None:
        return
    log.info("segunda abertura: trazendo a barra existente para a frente")
    bar.show()
    bar.raise_()
    bar.activateWindow()
    if not bar.details.isVisible():
        bar.toggle_details()


if __name__ == "__main__":
    raise SystemExit(main())
