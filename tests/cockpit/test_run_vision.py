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
    # depois, a calibração: cada imagem sozinha (sessões novas), para provar que o acréscimo é das duas
    assert images_sent(srv) == [[b64(ref.path), b64(img.path)], [b64(ref.path)], [b64(img.path)]]
    chat = [b for p, b in srv.requests if p == "/api/chat" and b["messages"][-1].get("images")][0]
    assert "1. referência do cômodo ref-quarto-r2@v1" in chat["messages"][-1]["content"]
    assert f"2. render do run {OK_RENDER}" in chat["messages"][-1]["content"]
    # registro: o ledger DA JANELA, uma linha por imagem com o papel, mesmo lote, SENT
    rows = sorted(win.image_ledger.list(session_id=tab.vision_result.session_id), key=lambda r: r.role)
    assert [(r.role, r.sha256, r.status) for r in rows] == [("reference", ref.sha256, "SENT"), ("render", img.sha256, "SENT")]
    assert len({r.batch_id for r in rows}) == 1
    assert all((r.model_id, r.purpose, r.requested_by, r.ref_id) == ("vision-d", "EVALUATION", "studio:review",
                                                                      "ref-quarto-r2") for r in rows)
    assert all(r.session_id == tab.vision_result.session_id for r in rows)
    # notificação: publicada pelo on_change do mesmo ledger
    cal = [r for r in win.image_ledger.list() if r.session_id != tab.vision_result.session_id]
    # revisão do #94 (SHOULD): a calibração tem finalidade própria; nunca conta como imagem avaliada
    assert sorted((r.role, r.sha256, r.purpose, r.requested_by) for r in cal) == [
        ("reference", ref.sha256, "CALIBRATION", "studio:review:calibracao"),
        ("render", img.sha256, "CALIBRATION", "studio:review:calibracao")]
    assert {r.purpose for r in rows} == {"EVALUATION"}
    assert len({r.batch_id for r in cal}) == 2                                   # uma imagem por turno de calibração
    notes = win.center.list(categories=["IMAGE"])                                 # o pedido + as 2 calibrações
    assert len(notes) == 3 and any(img.sha256[:12] in n.body and "vision-d" in n.body for n in notes)
    assert sum("calibração de tokens" in n.body for n in notes) == 2              # visível em Notificações
    assert "mede o custo de cada imagem" in tab.vision_send_btn.toolTip()         # NIT: chamadas extras ditas
    # confirmação: acréscimo MEDIDO contra a soma do custo de cada imagem sozinha, não o SENT
    line = tab.vision_usage.text()
    assert f"turno {13 + 2 * IMAGE_TOKENS} entrada" in line
    assert "entregue ao adaptador: 2 imagem(ns) SENT para vision-d" in line and img.sha256[:12] in line
    assert f"confirmado pelo provedor: o provedor contou {13 + 2 * IMAGE_TOKENS} tokens de entrada contra 13 só " \
           f"de texto (+{2 * IMAGE_TOKENS}); cada imagem sozinha custa {IMAGE_TOKENS} (reference) + {IMAGE_TOKENS} " \
           f"(render): o acréscimo só fecha com as 2 imagens" in line
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


REAL_REF, REAL_RENDER, REAL_TEXT = 2083, 1987, 41                            # qwen2.5vl:7b medido (cache_probe.txt)


@pytest.mark.parametrize("label,got,base,cal,ok", [
    # MUST do review: com 2 linhas SENT, o acréscimo de UMA imagem só NÃO confirma; o das duas confirma
    ("real: as duas", REAL_TEXT + 2042 + 1946, REAL_TEXT, [REAL_REF, REAL_RENDER], True),
    ("real: só a referência", REAL_REF, REAL_TEXT, [REAL_REF, REAL_RENDER], False),
    ("real: só o render", REAL_RENDER, REAL_TEXT, [REAL_REF, REAL_RENDER], False),
    ("revisor: base 300, +2000 de uma", 2300, 300, [2300, 2300], False),
    ("fake: as duas", 13 + 512, 13, [13 + 256, 13 + 256], True),
    ("fake: uma (+256)", 13 + 256, 13, [13 + 256, 13 + 256], False),
    ("sem calibração", 13 + 512, 13, None, False),
    ("calibração sem contagem", 13 + 512, 13, [13 + 256, None], False),
    ("sem contagem do pedido", None, 13, [269, 269], False),
    ("sem linha de base", 525, None, [269, 269], False),
    ("imagem barata demais", 13 + 40, 13, [13 + 20, 13 + 20], False)])
def test_confirmation_needs_the_measured_increase_of_every_image(label, got, base, cal, ok):
    from agent_bar.cockpit.run_vision import VisionResult
    from agent_bar.platform.usage import Metric, UsageSnapshot

    def snap(v):
        return None if v is None else UsageSnapshot("ollama", "m", input_tokens=Metric(v, "MEASURED", "test"))
    res = VisionResult("r", "m", "s", usage=snap(got), baseline=snap(base))
    res.deliveries = [object(), object()]                                         # type: ignore[list-item] - 2 SENT
    if cal is not None:
        res.calibration = [(role, "0" * 64, t, "" if t is not None else "sem contagem")
                           for role, t in zip(("reference", "render"), cal, strict=True)]
    confirmed, text = res.confirmation()
    assert confirmed is ok, (label, text)
    assert ("não confirmado" in text) is not ok or label == "imagem barata demais"


def test_context_tokens_is_refused_by_a_provider_that_cannot_honour_it():
    from agent_bar.platform.adapter import CapabilityUnsupported, SessionRequest
    from agent_bar.platform.sessions import SessionManager
    from tests.platform.fake_adapter import FakeAdapter
    sm = SessionManager({"fake": lambda: FakeAdapter()})
    try:
        with pytest.raises(CapabilityUnsupported, match="context_tokens"):
            sm.open("fake", SessionRequest(context_tokens=16384))
        assert sm.open("fake", SessionRequest())["state"]                        # sem o pedido, abre normal
    finally:
        sm.close_all()


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


# ------------------------------------------------------------------ S01C-F1-05: nota 1–5 pela rubrica, pelo caminho do #94
def _rubric_json(scores=None) -> str:
    import json as _json

    from tests.cockpit.test_evaluations import SCORES
    s = scores or SCORES
    return "Avaliação:\n" + _json.dumps({k: {"score": v, "justification": j} for k, (v, j) in s.items()})


def click_rubric_and_wait(tab):
    tab.rubric_btn.click()
    assert wait(lambda: tab.vision_result is not None or tab.vision_error, timeout=30)
    _APP.processEvents()


def test_rubric_click_records_the_scores_only_with_a_confirmed_delivery_and_shows_them(studio):
    from PySide6.QtCore import Qt

    from agent_bar.cockpit import evaluations as V
    from tests.platform.fake_ollama import fake_digest
    win, srv = studio
    tab = win.review
    srv.image_reply = _rubric_json()
    img = pick(win, "vision-d")
    ref = G.primary_image(G.reference_item(tab.gallery_items()))
    click_rubric_and_wait(tab)
    assert tab.evaluation_error == "", tab.evaluation_error
    item = tab.gallery.get(OK_RENDER)
    [ev], bad = tab.evaluation_store.for_item(item)
    assert bad == []
    assert ev["delivery"]["confirmed"] is True and "o acréscimo só fecha com as 2 imagens" in ev["delivery"]["confirmation"]
    assert ev["images"] == [{"ident": img.ident, "sha256": img.sha256}]
    assert ev["reference"]["sha256"] == ref.sha256
    assert {x["role"] for x in ev["delivery"]["images"]} == {"reference", "render"}
    assert ev["evaluator"]["digest"] == fake_digest("vision-d") and ev["evaluator"]["images"] == "SUPPORTED"
    assert {k: v["score"] for k, v in ev["scores"].items()} == {"fidelidade_referencia": 3, "cama_cabeceira_enxoval": 2,
                                                                "materiais_texturas": 3, "iluminacao": 4,
                                                                "mobiliario_detalhes": 3}
    assert not any(k in ev for k in ("total", "score_0_100", "human_approval", "verdict"))
    # exibida: na aba Avaliações e na tela principal (tile do run), sem soma; o MELHOR continua sem escolher
    assert "avaliação gravada" in tab.vision_status.text() and "Iluminação 4/5" in tab.vision_status.text()
    text = tab.evaluations_view.toPlainText()
    assert "1 avaliação(ões) válida(s)" in text and "entrega: confirmada pelo provedor" in text
    assert "Iluminação: 4/5" in text and "/100" not in text
    d = tab.design
    tile = next(d.history.item(i).text() for i in range(d.history.count())
                if d.history.item(i).data(Qt.ItemDataRole.UserRole) == OK_RENDER)
    assert "visual: vision-d (câmera cama_3q · luz dia): Fidelidade 3/5 · Cama 2/5 · Materiais 3/5 · Iluminação 4/5 · Mobiliário 3/5" in tile
    assert d.cards["melhor"].item is None                                         # F1-07 decide; avaliação não escolhe
    assert V.LABEL in tab.vision_status.text()


def test_rubric_without_provider_counts_records_nothing(studio):
    win, srv = studio
    tab = win.review
    srv.image_reply, srv.no_counts = _rubric_json(), True
    pick(win, "vision-d")
    click_rubric_and_wait(tab)
    assert tab.evaluation_error.startswith("avaliação NÃO gravada: não confirmado pelo provedor")
    assert tab.evaluation_store.for_item(tab.gallery.get(OK_RENDER)) == ([], [])


def test_rubric_answer_without_the_json_records_nothing(studio):
    win, srv = studio
    tab = win.review
    srv.image_reply = "A imagem mostra um quarto bonito."
    pick(win, "vision-d")
    click_rubric_and_wait(tab)
    assert "avaliação NÃO gravada: a resposta do modelo não tem o objeto JSON" in tab.evaluation_error
    assert tab.evaluation_store.for_item(tab.gallery.get(OK_RENDER)) == ([], [])


def test_rubric_with_a_model_without_vision_is_refused_and_nothing_is_sent(studio):
    win, srv = studio
    tab = win.review
    srv.image_reply = _rubric_json()
    pick(win, "chat-a")
    click_rubric_and_wait(tab)
    assert tab.vision_error.startswith("recusado, nada enviado: chat-a sem visão")
    assert win.image_ledger.list() == [] and tab.evaluation_store.for_item(tab.gallery.get(OK_RENDER)) == ([], [])


def test_rubric_without_the_model_digest_records_nothing(studio):
    win, srv = studio
    tab = win.review
    srv.image_reply, srv.no_digest = _rubric_json(), True
    pick(win, "vision-d")
    click_rubric_and_wait(tab)
    assert "avaliação NÃO gravada" in tab.evaluation_error and "sem digest do modelo" in tab.evaluation_error
    assert tab.evaluation_store.for_item(tab.gallery.get(OK_RENDER)) == ([], [])


# ----------------------------------------------------- revisão do #97: só quadro renderizado, nomeado antes de enviar
def _with_raw_first(tab, tmp, *, only_raw=False):
    """Põe uma vista crua (sem render) como 1ª imagem do run, como o cama_3q do rc5-render real."""
    import hashlib
    import shutil
    item = tab.gallery.get(OK_RENDER)
    raw_path = tmp / "cama_3q_cru.png"
    shutil.copyfile(item.images[0].path, raw_path)
    raw_path.write_bytes(raw_path.read_bytes() + b"cru")
    raw = G.GalleryImage("cama_3q", raw_path, hashlib.sha256(raw_path.read_bytes()).hexdigest(), "r", "views/cama_3q.png")
    item.images[:] = [raw] if only_raw else [raw, *item.images]
    return item, raw


def _select_run_and_model(win, model):
    tab = win.review
    t = tab.gallery_tree
    t.setCurrentItem(None)
    t.setCurrentItem(next(t.topLevelItem(i) for i in range(t.topLevelItemCount())
                          if t.topLevelItem(i).text(3) == OK_RENDER))                # a lista de imagens não é tocada
    tab.vision_models_btn.click()
    assert wait(lambda: tab.vision_model_box.count() > 0)
    tab.vision_model_box.setCurrentIndex(tab.vision_model_box.findData(model))


def test_rubric_with_a_raw_first_image_sends_the_preferred_rendered_frame_and_names_it(studio, tmp_path):
    from PySide6.QtCore import Qt

    win, srv = studio
    tab = win.review
    srv.image_reply = _rubric_json()
    item, raw = _with_raw_first(tab, tmp_path)
    _select_run_and_model(win, "vision-d")
    cur = tab.gallery_images.currentItem().data(Qt.ItemDataRole.UserRole)        # a lista abre no quadro preferido
    assert Path(cur).name == next(i for i in item.images if i.key == "dia_cama_3q").path.name
    tab.rubric_btn.click()
    status = tab.vision_status.text()                                             # antes do envio
    assert "câmera cama_3q · luz dia (sha " in status and "vista" not in status.split("fora da rubrica")[0]
    assert tab.vision_sending == ["dia_cama_3q"]
    assert wait(lambda: tab.vision_result is not None or tab.vision_error, timeout=30)
    sent = images_sent(srv)[0]
    assert base64.b64encode(raw.path.read_bytes()).decode() not in sent               # a vista crua nunca vai
    assert sent[1] == b64(next(i for i in item.images if i.key == "dia_cama_3q").path)
    [ev], _ = tab.evaluation_store.for_item(item)
    assert ev["camera"] == "dia_cama_3q"


def test_rubric_on_a_run_without_rendered_frames_is_refused_and_nothing_is_sent(studio, tmp_path):
    win, srv = studio
    tab = win.review
    srv.image_reply = _rubric_json()
    _with_raw_first(tab, tmp_path, only_raw=True)
    _select_run_and_model(win, "vision-d")
    tab.rubric_btn.click()
    _APP.processEvents()
    assert tab.vision_error.startswith("recusado, nada enviado") and "não tem quadro renderizado" in tab.vision_error
    assert win.image_ledger.list() == [] and not [p for p, _ in srv.requests if p == "/api/chat"]


def test_rubric_when_only_the_raw_view_is_selected_uses_the_preferred_rendered_frame_and_says_so(studio, tmp_path):
    win, srv = studio
    tab = win.review
    srv.image_reply = _rubric_json()
    item, raw = _with_raw_first(tab, tmp_path)
    _select_run_and_model(win, "vision-d")
    tab.gallery_images.clearSelection()
    tab.gallery_images.item(0).setSelected(True)                                 # Felipe marcou só a vista crua
    assert tab.selected_image_keys() == ["cama_3q"]
    tab.rubric_btn.click()
    assert tab.vision_sending == ["dia_cama_3q"]                                   # dia antes de noite (ordem fixa)
    assert "vista crua fora da rubrica: cama_3q" in tab.vision_status.text()
    assert wait(lambda: tab.vision_result is not None or tab.vision_error, timeout=30)
