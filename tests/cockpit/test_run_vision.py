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


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def test_click_sends_reference_and_render_in_one_request_records_both_and_confirms(studio):
    """MUST do review do #94: a referência e o render chegam JUNTOS ao provedor; reprova se só um chegar."""
    win, srv = studio
    tab = win.review
    img = pick(win, "vision-d")
    ref = G.primary_image(G.reference_item(tab.gallery_items()))
    click_and_wait(tab)
    assert tab.vision_error == ""
    # entrega: um único pedido com as DUAS imagens, referência primeiro, os bytes exatos dos arquivos conferidos
    assert images_sent(srv) == [[b64(ref.path), b64(img.path)]]
    chat = [b for p, b in srv.requests if p == "/api/chat" and b["messages"][-1].get("images")][0]
    assert "1. referência do cômodo ref-quarto-r2@v1" in chat["messages"][-1]["content"]
    assert f"2. render do run {OK_RENDER}" in chat["messages"][-1]["content"]
    # registro: o ledger DA JANELA, uma linha por imagem com o papel, mesmo lote, SENT
    rows = sorted(win.image_ledger.list(), key=lambda r: r.role)
    assert [(r.role, r.sha256, r.status) for r in rows] == [("reference", ref.sha256, "SENT"), ("render", img.sha256, "SENT")]
    assert len({r.batch_id for r in rows}) == 1
    assert all((r.model_id, r.purpose, r.requested_by, r.ref_id) == ("vision-d", "EVALUATION", "studio:review",
                                                                      "ref-quarto-r2") for r in rows)
    assert all(r.session_id == tab.vision_result.session_id for r in rows)
    # notificação: publicada pelo on_change do mesmo ledger
    notes = win.center.list(categories=["IMAGE"])
    assert len(notes) == 1 and img.sha256[:12] in notes[0].body and "vision-d" in notes[0].body
    # confirmação: contagem MEDIDA do provedor contra a linha de base só de texto, não o SENT
    line = tab.vision_usage.text()
    assert f"turno {13 + 2 * IMAGE_TOKENS} entrada" in line
    assert "entregue ao adaptador: 2 imagem(ns) SENT para vision-d" in line and img.sha256[:12] in line
    assert f"confirmado pelo provedor: o provedor contou {13 + 2 * IMAGE_TOKENS} tokens de entrada contra 13 só " \
           f"de texto (+{2 * IMAGE_TOKENS}): compatível com 2 imagem(ns)" in line
    assert "imagens=2" in tab.vision_text.toPlainText()
    assert "não é nota nem veredito" in tab.vision_status.text()
    assert win.platform_sessions.get(rows[0].session_id)["state"] == "CLOSED"      # sessão não fica pendurada
    base = [b for p, b in srv.requests if p == "/api/chat" and not b["messages"][-1].get("images")]
    assert len(base) == 1 and base[0]["messages"][-1]["content"] == chat["messages"][-1]["content"]   # mesmo texto


def test_without_provider_counts_it_says_delivered_to_the_adapter_not_arrived(studio):
    win, srv = studio
    tab = win.review
    pick(win, "vision-d")
    tab.vision_prompt.setPlainText("descreva semcontagem")                    # o fake não manda os contadores
    click_and_wait(tab)
    line = tab.vision_usage.text()
    assert "não confirmado: entregue ao adaptador; contagem do provedor indisponível" in line
    assert "confirmado pelo provedor" not in line and "chegou" not in line.replace("não confirmado que", "")


def test_reference_whose_file_changed_after_the_gallery_is_refused_and_nothing_is_sent(studio):
    win, srv = studio
    tab = win.review
    pick(win, "vision-d")
    ref = G.primary_image(G.reference_item(tab.gallery_items()))
    ref.path.write_bytes(ref.path.read_bytes() + b"mudou")
    click_and_wait(tab)
    assert "recusado, nada enviado: referência" in tab.vision_error and "diverge do registrado" in tab.vision_error
    assert win.image_ledger.list() == [] and not [p for p, _ in srv.requests if p == "/api/chat"]


def test_room_without_a_reference_image_is_refused(tmp_path, monkeypatch):
    from agent_bar.cockpit.run_vision import RunVision, VisionRefused
    from agent_bar.cockpit.ui.window import _ollama_adapter
    from agent_bar.platform.image_ledger import ImageLedger
    from agent_bar.platform.sessions import SessionManager
    with FakeOllama() as srv:
        monkeypatch.setenv("OLLAMA_HOST", srv.endpoint)
        ledger = ImageLedger(tmp_path / "p.sqlite")
        sm = SessionManager({"ollama": _ollama_adapter}, image_ledger=ledger)
        try:
            item = _render_item(tmp_path)
            with pytest.raises(VisionRefused, match="sem referência"):
                RunVision(sm, ledger).ask(item, [item.images[0].key], "vision-d", reference=None)
            assert ledger.list() == [] and not [p for p, _ in srv.requests if p == "/api/chat"]
        finally:
            sm.close_all()
            ledger.close()


def _render_item(tmp_path: Path) -> G.GalleryItem:
    import hashlib

    from tests.cockpit.evidence_fixture import png
    p = tmp_path / "render.png"
    p.write_bytes(png("render"))
    return G.GalleryItem(G.ENGINE, PLANT, ROOM, "r", "render",
                         images=[G.GalleryImage("dia_cama_3q", p, hashlib.sha256(p.read_bytes()).hexdigest(), "l", "x")])


@pytest.mark.parametrize("got,base,n,ok", [(13 + 512, 13, 2, True), (13 + 100, 13, 2, False), (None, 13, 2, False),
                                           (525, None, 2, False)])
def test_confirmation_needs_measured_counts_above_the_text_baseline(got, base, n, ok):
    from agent_bar.cockpit.run_vision import VisionResult
    from agent_bar.platform.usage import Metric, UsageSnapshot

    def snap(v):
        return None if v is None else UsageSnapshot("ollama", "m", input_tokens=Metric(v, "MEASURED", "test"))
    res = VisionResult("r", "m", "s", usage=snap(got), baseline=snap(base))
    res.deliveries = [object()] * n                                              # type: ignore[list-item]
    assert res.confirmation()[0] is ok


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
    """Mesmo caminho do clique, contra o Ollama local: a referência r2 e um render de verdade no mesmo pedido."""
    import hashlib

    from agent_bar.cockpit.run_vision import RunVision
    from agent_bar.cockpit.ui.window import _ollama_adapter
    from agent_bar.platform.image_ledger import ImageLedger
    from agent_bar.platform.sessions import SessionManager

    def item(source, item_id, kind, key, env, default):
        src = Path(os.environ.get(env, default))
        return G.GalleryItem(source, PLANT, ROOM, item_id, kind, images=[
            G.GalleryImage(key, src, hashlib.sha256(src.read_bytes()).hexdigest(), "local", src.name)])
    ref = item(G.REFERENCE, "ref-r2@v2", "reference", "r2", "AGENT_BAR_REAL_RUN_VISION_REF",
               r"E:\Claude\data\runs\p2s-suite01-convergence\reference-r2.webp")
    run = item(G.PUBLISHED, "real-render", "render", "render", "AGENT_BAR_REAL_RUN_VISION_IMAGE",
               r"E:\Claude\data\runs\p2s-suite01-convergence\reference-r2.webp")
    ledger = ImageLedger(tmp_path / "p.sqlite")
    sm = SessionManager({"ollama": _ollama_adapter}, image_ledger=ledger)
    try:
        res = RunVision(sm, ledger).ask(run, ["render"], REAL, reference=ref).result(900)
        assert res.text.strip() and {d.role for d in res.deliveries} == {"reference", "render"}
        assert all(d.status == "SENT" for d in res.deliveries)
        assert res.usage is not None and res.usage.input_tokens.state == "MEASURED"
        ok, confirm = res.confirmation()
        print(f"\nREAL {REAL}: {res.delivery_line()}\n{'CONFIRMADO' if ok else 'NAO CONFIRMADO'}: {confirm}\n"
              f"{res.text[:600]}")
        assert ok, confirm
    finally:
        sm.close_all()
        ledger.close()
