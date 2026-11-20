"""app.py — entrada da barra: `python -m agent_bar.app [--once-json] [--screenshot ARQ.png]`."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent_bar")
    ap.add_argument("--once-json", action="store_true", help="imprime o diagnostico da coleta e sai (sem GUI)")
    ap.add_argument("--screenshot", help="grava um PNG da barra (e do painel expandido) apos a 1a coleta e sai")
    ap.add_argument("--open-panel", action="store_true", help="abre o painel expandido ao iniciar (evidencia/teste)")
    ap.add_argument("--full", action="store_true", help="com --screenshot: grava tambem a Central completa (ARQ_full.png)")
    ns = ap.parse_args(argv)
    if ns.once_json:
        from agent_bar import collectors as col
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:                                           # redirecionado no Windows sai em cp1252 sem isto (S00-07 D7)
            reconfigure(encoding="utf-8")
        print(col.collect_snapshot(claude_login=col.claude_login_state, codex_login=col.codex_login_state).diagnostic())
        return 0
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 nao instalado: uv pip install PySide6-Essentials (extra agent-bar)", file=sys.stderr)
        return 2
    from agent_bar import settings as cfg
    from agent_bar.window import Bar
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    bar = Bar(cfg.load())
    bar.show()
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


if __name__ == "__main__":
    raise SystemExit(main())
