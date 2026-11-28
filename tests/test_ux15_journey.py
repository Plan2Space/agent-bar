"""UX-15 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9): aceite OFFSCREEN da jornada do Felipe, ponta a ponta, pelo caminho real do
atalho (`AppCore` + `start_main`):

  abrir → Design da Suíte → referência e último render → comparar → comentar → Chat preenchido (sem enviar)

Critério: a falta de QUALQUER passo reprova (cada passo tem o seu assert, com o nome do passo); nenhuma janela além da
principal aparece em momento algum. Dados sintéticos (gallery_fixture), agente que não cria processo, `gh` falso; sem
rede. O aceite real, com capturas e PIDs, é o UX-16."""
from __future__ import annotations

import concurrent.futures as cf
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar import app as A  # noqa: E402
from agent_bar import settings as cfg  # noqa: E402
from agent_bar.cockpit import evidence as E  # noqa: E402
from agent_bar.cockpit import gallery as G  # noqa: E402
from agent_bar.cockpit import review_comments as RC  # noqa: E402
from agent_bar.core import AppCore  # noqa: E402
from tests.cockpit.gallery_fixture import PLANT, ROOM, build_all  # noqa: E402

_APP = QApplication.instance() or QApplication([])
COCKPIT = Path(__file__).parent / "cockpit"
FELIPE = "Gostei mais da iluminação anterior, mas quero o edredom mais natural."


class NoSpawnClient:
    """A jornada termina no Chat preenchido; nenhum agente precisa existir (e nenhum processo é criado)."""

    def __init__(self, argv, cwd, **_kw):
        self.connects = 0

    def connect(self, _sid=None):
        self.connects += 1
        return cf.Future()

    def close(self):
        f = cf.Future()
        f.set_result(None)
        return f


def wait(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def visible_windows() -> set:
    return {w for w in QApplication.topLevelWidgets() if w.isVisible() and w.isWindow()}


@pytest.fixture
def journey(tmp_path, monkeypatch):
    from agent_bar.cockpit.github import GhClient
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    import agent_bar.core as C
    monkeypatch.setattr(C.col, "collect_snapshot", lambda **_kw: None)              # núcleo de verdade, sem rede
    ev, engine, dl = build_all(tmp_path)
    (engine / ".git").mkdir()
    monkeypatch.setenv("AGENT_BAR_ENGINE_DIR", str(engine))
    (tmp_path / "ws").mkdir()

    def factory():
        return CockpitWindow(
            gh=GhClient(exe=[sys.executable, str(COCKPIT / "fake_gh.py")]), repos=["o/r"], client_factory=NoSpawnClient,
            profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(COCKPIT / "fake_acp_agent.py")))],
            workspaces_fn=lambda: [tmp_path / "ws"],
            review_kwargs={"root_fn": lambda: ev, "load_fn": lambda r: E.load_catalog(r, expected={}),
                           "gallery_fn": lambda cat: G.build_gallery(cat, engine, dl, rooms=[(PLANT, ROOM)]),
                           "comment_store": RC.CommentStore(tmp_path / "data")})
    core = AppCore(cfg.Settings(local_interval_s=60), quit_fn=lambda: None, tray_available=lambda: True,
                   cockpit_factory=factory)
    before = visible_windows()
    core.start()
    try:
        yield core, before
    finally:
        core.shutdown()
        for w in QApplication.topLevelWidgets():
            if w not in before:
                w.hide()
                w.deleteLater()
        _APP.processEvents()


def test_the_whole_journey_open_suite_reference_last_compare_comment(journey):
    core, before = journey

    def only_the_main_window(step: str) -> None:
        assert visible_windows() - before == {core.cockpit}, f"{step}: abriu janela além da principal"

    # 1. abrir: o atalho abre só o Cockpit (D-UX-1)
    A.start_main(core)
    _APP.processEvents()
    win = core.cockpit
    assert win is not None and win.isVisible(), "passo 1 (abrir): o Cockpit não abriu"
    only_the_main_window("passo 1 (abrir)")

    # 2. Design da Suíte: a área Design mostra o cômodo
    win.show_page(win.review)
    assert wait(lambda: win.review.design.history.count() > 0), "passo 2 (Design): a galeria do cômodo não carregou"
    assert win.review.room_box.currentData() == (PLANT, ROOM), "passo 2 (Design): o cômodo não é o escolhido"
    d = win.review.design
    only_the_main_window("passo 2 (Design)")

    # 3. referência e último render, grandes, na tela principal (UX-14)
    ref, last = d.cards["referência"], d.cards["último"]
    assert ref.item is not None and ref.image is not None, "passo 3: sem referência vigente na tela"
    assert last.item is not None and last.image is not None, "passo 3: sem último render na tela"
    assert last.item is G.latest_render(d.renders, camera=d.camera), "passo 3: o cartão não é o último render"
    only_the_main_window("passo 3 (referência e último)")

    # 4. comparar: com a referência e com a versão anterior, na mesma tela
    d.compare_with_reference()
    assert d.compare.isVisibleTo(d) and d.compared_images is not None, "passo 4: comparar com a referência não abriu"
    assert d.compared is not None and d.compared[1].source == G.REFERENCE, "passo 4: o outro lado não é a referência"
    prev = d.previous_of(last.item)
    assert prev is not None, "passo 4: não há versão anterior para comparar"
    d.compare_with_previous()
    assert d.compared is not None and (d.compared[0].item_id, d.compared[1].item_id) == (last.item.item_id, prev.item_id), \
        "passo 4: comparar com a versão anterior não mostrou a anterior"
    only_the_main_window("passo 4 (comparar)")

    # 5. comentar: o Chat abre preenchido com o contexto, e NADA é enviado
    d.comment.setText(FELIPE)
    d.comment_btn.click()
    assert wait(lambda: win.current_page() is win.sessions), "passo 5: comentar não levou ao Chat"
    text = win.chat.input.toPlainText()
    assert FELIPE in text and text == RC.chat_request(win.review.last_comment), "passo 5: o Chat não tem o pedido"
    t_end = time.monotonic() + 1.0
    while time.monotonic() < t_end:                                                    # tempo para um envio indevido
        _APP.processEvents()
        time.sleep(0.02)
    assert win.chat.chat.messages == [], "passo 5: algo foi enviado sem o Felipe"
    only_the_main_window("passo 5 (comentar)")
