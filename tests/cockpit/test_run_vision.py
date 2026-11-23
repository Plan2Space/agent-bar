""""Avaliar este run" no app: do clique na Revisão visual até o Ollama (HTTP falso), sem espião.

Usa as instâncias REAIS da janela (CockpitWindow): o SessionManager da plataforma e o mesmo ImageLedger que alimenta
as Notificações. Prova a entrega (bytes da imagem no provedor), o registro (ledger SENT com sha/propósito/referência),
a Notificação e a linha de uso; e as duas recusas sem envio: modelo sem visão e imagem com sha divergente.
O caminho não grava nota nem veredito.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit import evidence as E  # noqa: E402
from agent_bar.cockpit import gallery as G  # noqa: E402
from tests.cockpit.gallery_fixture import OK_RENDER, PLANT, ROOM, build_all  # noqa: E402
from tests.platform.fake_ollama import IMAGE_TOKENS, FakeOllama  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def studio(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    ev, engine, dl = build_all(tmp_path)
    with FakeOllama() as srv:
        monkeypatch.setenv("OLLAMA_HOST", srv.endpoint)
        stub = Path(__file__).with_name("fake_acp_agent.py")
        win = CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                            workspaces_fn=lambda: [tmp_path / "x"],
                            review_kwargs={"root_fn": lambda: ev,
                                           "load_fn": lambda r: E.load_catalog(r, expected={}),
                                           "gallery_fn": lambda cat: G.build_gallery(cat, engine, dl,
                                                                                     rooms=[(PLANT, ROOM)])})
        try:
            tab = win.review
            tab.reload()
            assert wait(lambda: tab.gallery is not None and tab.gallery_tree.topLevelItemCount() > 0)
            yield win, srv
        finally:
            win.shutdown()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pick(win, model: str) -> object:
    """Seleciona o run e a primeira imagem na galeria, carrega os modelos e escolhe `model`."""
    tab = win.review
    t = tab.gallery_tree
    t.setCurrentItem(next(t.topLevelItem(i) for i in range(t.topLevelItemCount())
                          if t.topLevelItem(i).text(3) == OK_RENDER))
    tab.gallery_images.item(0).setSelected(True)
    tab.vision_models_btn.click()
    assert wait(lambda: tab.vision_model_box.count() > 0)
    tab.vision_model_box.setCurrentIndex(tab.vision_model_box.findData(model))
    return tab.gallery.get(OK_RENDER).images[0]


def click_and_wait(tab):
    tab.vision_send_btn.click()
    assert wait(lambda: tab.vision_result is not None or tab.vision_error, timeout=30)


def images_sent(srv) -> list:
    return [b["messages"][-1].get("images") for p, b in srv.requests if p == "/api/chat" and b["messages"][-1].get("images")]


def test_click_sends_the_image_records_it_notifies_and_shows_usage(studio):
    win, srv = studio
    tab = win.review
    img = pick(win, "vision-d")
    click_and_wait(tab)
    assert tab.vision_error == ""
    # entrega: os bytes do arquivo conferido chegaram ao provedor
    assert images_sent(srv) == [[base64.b64encode(img.path.read_bytes()).decode()]]
    # registro: o ledger DA JANELA, SENT, com sha, propósito, quem pediu e a referência do cômodo
    [row] = win.image_ledger.list()
    assert (row.sha256, row.status, row.model_id, row.purpose, row.requested_by) == (
        img.sha256, "SENT", "vision-d", "EVALUATION", "studio:review")
    assert row.ref_id == "ref-quarto-r2" and row.session_id == tab.vision_result.session_id
    # notificação: publicada pelo on_change do mesmo ledger
    notes = win.center.list(categories=["IMAGE"])
    assert len(notes) == 1 and img.sha256[:12] in notes[0].body and "vision-d" in notes[0].body
    # uso e entrega na mesma linha; a imagem está contada nos tokens de entrada
    line = tab.vision_usage.text()
    assert f"turno {13 + IMAGE_TOKENS} entrada" in line
    assert "entrega: 1 imagem(ns) SENT para vision-d" in line and img.sha256[:12] in line
    assert "imagens=1" in tab.vision_text.toPlainText()
    assert "não é nota nem veredito" in tab.vision_status.text()
    assert win.platform_sessions.get(row.session_id)["state"] == "CLOSED"          # sessão não fica pendurada


def test_model_without_vision_is_refused_and_nothing_is_sent(studio):
    win, srv = studio
    tab = win.review
    pick(win, "chat-a")
    click_and_wait(tab)
    assert tab.vision_result is None
    assert tab.vision_error.startswith("recusado, nada enviado: chat-a sem visão (images=UNSUPPORTED)")
    assert win.image_ledger.list() == [] and images_sent(srv) == []
    assert not [p for p, _ in srv.requests if p == "/api/chat"]
    assert win.center.list(categories=["IMAGE"]) == []


def test_image_whose_sha256_differs_from_the_file_is_refused(studio):
    win, srv = studio
    tab = win.review
    img = pick(win, "vision-d")
    img.path.write_bytes(img.path.read_bytes() + b"mudou depois da galeria")
    click_and_wait(tab)
    assert tab.vision_result is None
    assert "recusado, nada enviado" in tab.vision_error and "diverge do registrado" in tab.vision_error
    assert win.image_ledger.list() == [] and images_sent(srv) == []
    assert not [p for p, _ in srv.requests if p == "/api/chat"]


def test_without_a_selected_image_nothing_is_sent(studio):
    win, srv = studio
    tab = win.review
    pick(win, "vision-d")
    tab.gallery_images.clearSelection()
    click_and_wait(tab)
    assert "nenhuma imagem escolhida" in tab.vision_error and win.image_ledger.list() == []


REAL = os.environ.get("AGENT_BAR_REAL_RUN_VISION")                             # ex.: qwen2.5vl:7b


@pytest.mark.skipif(not REAL, reason="opt-in: AGENT_BAR_REAL_RUN_VISION=<modelo com visão no Ollama local>")
def test_real_ollama_vision_model_through_the_window(tmp_path):
    """Mesmo caminho do clique, contra o Ollama local e uma imagem de verdade (cópia da r2)."""
    from agent_bar.cockpit.run_vision import RunVision
    from agent_bar.cockpit.ui.window import _ollama_adapter
    from agent_bar.platform.image_ledger import ImageLedger
    from agent_bar.platform.sessions import SessionManager
    src = Path(os.environ.get("AGENT_BAR_REAL_RUN_VISION_IMAGE",
                              r"E:\Claude\data\runs\p2s-suite01-convergence\reference-r2.webp"))
    import hashlib
    data = src.read_bytes()
    item = G.GalleryItem(G.ENGINE, PLANT, ROOM, "real", "render", images=[
        G.GalleryImage("r2", src, hashlib.sha256(data).hexdigest(), "local", src.name)])
    ledger = ImageLedger(tmp_path / "p.sqlite")
    sm = SessionManager({"ollama": _ollama_adapter}, image_ledger=ledger)
    try:
        res = RunVision(sm, ledger).ask(item, ["r2"], REAL).result(600)
        assert res.text.strip() and res.deliveries[0].status == "SENT"
        assert res.usage is not None and res.usage.input_tokens.state == "MEASURED"
        print(f"\nREAL {REAL}: {res.delivery_line()} · entrada {res.usage.input_tokens.value}\n{res.text[:400]}")
    finally:
        sm.close_all()
        ledger.close()
