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
    ap.add_argument("--cockpit", action="store_true", help="(compatibilidade) o Cockpit ja e a janela principal")
    ap.add_argument("--full", action="store_true", help="com --screenshot: grava tambem a Central completa (ARQ_full.png)")
    ns = ap.parse_args(argv)
    from agent_bar import startup

    if ns.once_json:
        startup.load_local_env(LOCAL_ENV)
        from agent_bar import collectors as col
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:                                           # redirecionado no Windows sai em cp1252 sem isto (S00-07 D7)
            reconfigure(encoding="utf-8")
        print(col.collect_snapshot(claude_login=col.claude_login_state).diagnostic())
        return 0
    log_path = None
    try:
        log_path = startup.configure_logging()
        log = logging.getLogger(startup.LOGGER)
        applied = startup.load_local_env(LOCAL_ENV)
        log.info("iniciando pid=%s exe=%s cwd=%s repo=%s local.env=%s", os.getpid(), sys.executable, os.getcwd(), ROOT, applied or "nenhum")
        from agent_bar import runtime_pin
        runtime_pin.log_status(runtime_pin.check())
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
    from agent_bar.startup import APP_NAME, checkout_version
    app.setApplicationName(APP_NAME)
    if ICON.is_file():
        app.setWindowIcon(QIcon(str(ICON)))
    instance = None
    if not ns.screenshot:                                                     # captura de evidencia roda ao lado da barra aberta
        from agent_bar.single_instance import SingleInstance
        from agent_bar.startup import log_dir
        holder: dict = {}
        instance = SingleInstance(lock_dir=log_dir().parent, version=checkout_version(ROOT))
        activate = lambda: _bring_forward(holder.get("bar"), log, holder.get("core"))  # noqa: E731
        quit_for_update = lambda: _quit_for_update(holder.get("bar"), log, holder.get("core"))  # noqa: E731
        if not instance.acquire(on_activate=activate, on_quit=quit_for_update, notify=False):
            if not _replace_older_instance(app, instance, activate, quit_for_update, log):
                return 0
        app.aboutToQuit.connect(instance.release)
    core = build_core(cfg.load())
    core.bar_factory = lambda: Bar(core.settings, core=core)
    if ns.screenshot or ns.open_panel:                                         # evidência da própria barra
        core.bar = core.bar_factory()
    core.start()
    bar = start_main(core, screenshot=bool(ns.screenshot))
    if instance is not None:
        holder["bar"], holder["core"] = bar, core                               # a instância única fala com o núcleo
    log.info("janela principal: Cockpit; barra flutuante %s", "visivel" if bar is not None and bar.isVisible() else "desligada")
    if ns.open_panel and bar is not None:                                      # evidência do painel: a barra aparece
        bar.show()
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
        delay = os.environ.get("AGENT_BAR_SCREENSHOT_DELAY_S", "7")                # coletas em fundo (aprovacoes) podem pedir mais tempo
        QTimer.singleShot(int(float(delay) * 1000) if delay.replace(".", "", 1).isdigit() else 7000, shot)
    return app.exec()


def start_main(core, screenshot: bool = False):
    """UX-03 (D-UX-1): o atalho abre o Cockpit, que é a janela principal. A barra flutuante só aparece se estiver ligada
    (bandeja > Barra flutuante) ou para a captura de evidência dela; a Central legada nunca abre sozinha. Devolve a barra
    (ou None)."""
    if core.settings.bar_enabled and core.bar is None and core.bar_factory is not None:
        core.bar = core.bar_factory()
    bar = core.bar
    if bar is not None and (core.settings.bar_enabled or screenshot):
        bar.show()
    if not screenshot:
        core.open_cockpit()
    return bar


def build_core(settings):
    """UX-01: o núcleo sem janela. O aviso "PR precisa de você" vira item em Notificações no mesmo SQLite que a aba
    Notificações do Cockpit lê (`<dados do cockpit>/permissions.sqlite`)."""
    from agent_bar.cockpit.notifications import NotificationCenter
    from agent_bar.cockpit.store import data_dir
    from agent_bar.core import AppCore
    return AppCore(settings, center=NotificationCenter(data_dir() / "permissions.sqlite"))


def _replace_older_instance(app, instance, activate, quit_for_update, log: logging.Logger) -> bool:
    """Ja existe um P2S Studio aberto. Se ele e de um commit mais velho que este checkout, pergunta se troca pela versao
    nova; sim = pede para a antiga se fechar (ela fecha normalmente, inclusive os agentes do cockpit) e assume. Senao, so
    traz a existente para a frente. True = esta abertura continua."""
    import time

    from PySide6.QtWidgets import QMessageBox
    info = instance.running_info() or {}
    if not instance.newer_available():
        instance.send("activate")
        log.info("ja existe um P2S Studio aberto (mesma versao ou versao desconhecida): pedido para vir para a frente; esta abertura sai")
        return False
    old, new = str(info.get("version", ""))[:7], instance.version[:7]
    answer = QMessageBox.question(None, "P2S Studio — versão nova disponível",
                                  f"Já existe um P2S Studio aberto com uma versão mais velha ({old}) que a deste checkout ({new}).\n\n"
                                  "Fechar a antiga e abrir a nova?")
    if answer != QMessageBox.StandardButton.Yes:
        instance.send("activate")
        log.info("versao nova disponivel (%s -> %s), mas o usuario manteve a antiga", old, new)
        return False
    log.info("versao nova disponivel (%s -> %s): pedindo para a antiga se fechar", old, new)
    instance.send("quit")
    end = time.monotonic() + 20
    while time.monotonic() < end:
        app.processEvents()
        if instance.acquire(on_activate=activate, on_quit=quit_for_update, notify=False):
            return True
        time.sleep(0.25)
    QMessageBox.warning(None, "P2S Studio", "A versão antiga não fechou em 20 s. Use “Sair do P2S Studio” (ícone da bandeja) nela e abra de novo.")
    log.info("a instancia antiga nao fechou em 20 s; esta abertura sai")
    return False


def _quit_for_update(bar, log: logging.Logger, core=None) -> None:
    """Pedido de uma versao nova: fecha esta instancia normalmente (encerra os agentes do cockpit e a coleta). Com ou sem
    barra: quem encerra e o nucleo."""
    log.info("uma versao nova pediu para esta instancia fechar")
    if bar is not None:
        bar.close_app()                                                         # fecha o painel e chama core.shutdown()
    elif core is not None:
        core.shutdown()
    else:
        from PySide6.QtWidgets import QApplication
        QApplication.quit()


def _bring_forward(bar, log: logging.Logger, core=None) -> None:
    """Segunda abertura (UX-03): o Cockpit existente vem para a frente (sai da bandeja), e a barra só se estiver ligada.
    A Central legada nunca abre. Nada e iniciado ou parado."""
    if core is not None:
        log.info("segunda abertura: trazendo o Cockpit existente para a frente")
        core.open_cockpit()
    if bar is not None and (core is None or core.settings.bar_enabled):
        bar.show()
        bar.raise_()


if __name__ == "__main__":
    raise SystemExit(main())
