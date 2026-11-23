"""S01C-F1-11: comentário na Galeria de design → item R41 (sem triagem) → Chat preenchido com contexto estruturado.

Reprova se o Chat receber nome/caminho de arquivo em vez do contexto (cômodo, run, câmera, referência, comparação), ou
se a mensagem for enviada sozinha. O passo 10 da jornada do Felipe é o teste de janela, com a frase dele.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit import evidence as E  # noqa: E402
from agent_bar.cockpit import gallery as G  # noqa: E402
from agent_bar.cockpit import review_comments as RC  # noqa: E402
from tests.cockpit.gallery_fixture import BAD_SHA, PLANT, ROOM, UNPUB_FAIL, build_all  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FELIPE = "Gostei mais da iluminação anterior, mas quero o edredom mais natural."


def wait(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def g(tmp_path) -> G.Gallery:
    ev, engine, dl = build_all(tmp_path)
    return G.build_gallery(E.load_catalog(ev, expected={}), engine, dl, rooms=[(PLANT, ROOM)])


def _ref(g):
    return next(r for r in g.of(PLANT, ROOM, sources=[G.REFERENCE]) if r.images)


# ------------------------------------------------------------------------------------------------ unidade
def test_chat_request_is_structured_context_without_file_names(g):
    item, prev = g.get(UNPUB_FAIL), g.get(BAD_SHA)
    c = RC.new_comment(FELIPE, item, item.images[0], reference=_ref(g), compared=(prev, prev.images[0]),
                       author="human", clock=lambda: 1795400000.0)
    text = RC.chat_request(c)
    for piece in (f'Comentário: "{FELIPE}"', f"cômodo: {PLANT} / {ROOM}", f"run visível: {UNPUB_FAIL}",
                  "câmera: dia_cama_3q", f"comparado com a versão anterior: {BAD_SHA}", "referência do cômodo: ref-quarto-r2@v1",
                  f"comentário {c.id} (R41, sem triagem", "Nada vai para o motor sem confirmação"):
        assert piece in text, piece
    assert not RC.mentions_files(text)
    for leak in (".png", "renders", "artifacts", str(item.images[0].path)):
        assert leak not in text
    d = c.to_dict()
    assert (d["category"], d["nature"], d["acao"], d["triage"]) == ("", "", "", "sem triagem")
    assert d["created_at"] == "2026-11-23T02:13:20Z" and d["author"] == "human"
    assert d["camera"] == "dia_cama_3q" and d["image_sha256"] == item.images[0].sha256


@pytest.mark.parametrize("text,leaks", [
    ("veja renders/dia_cama_3q.png", True), (r"E:\Claude\x", True), ("abra request.json", True),
    ("cômodo: planta_74 / suite_01", False), ("câmera entardecer_frente", False), ("23/11 06:16 UTC", False),
    ("artifacts/x_y/run", True)])
def test_file_name_detector(text, leaks):
    assert RC.mentions_files(text) is leaks


def test_a_request_that_would_carry_a_file_name_is_refused(g):
    item = g.get(UNPUB_FAIL)
    img = item.images[0]
    filey = G.GalleryImage("dia_cama_3q.png", img.path, img.sha256, img.repo, img.rel)   # quadro com nome de arquivo
    item.images.append(filey)
    c = RC.new_comment(FELIPE, item, filey, author="human")
    with pytest.raises(RC.CommentRejected):
        RC.chat_request(c)


@pytest.mark.parametrize("kw,why", [({"text": "   "}, "vazio"), ({"author": "agent:claude"}, "humano")])
def test_empty_or_non_human_comment_is_refused(g, kw, why):
    item = g.get(UNPUB_FAIL)
    with pytest.raises(RC.CommentRejected, match=why):
        RC.new_comment(kw.get("text", FELIPE), item, item.images[0], author=kw.get("author", "human"))


def test_frame_from_another_run_is_refused(g):
    with pytest.raises(RC.CommentRejected):
        RC.new_comment(FELIPE, g.get(UNPUB_FAIL), g.get(BAD_SHA).images[0], author="human")


def test_store_writes_outside_the_sources(g, tmp_path):
    item = g.get(UNPUB_FAIL)
    c = RC.new_comment(FELIPE, item, item.images[0], author="human")
    roots = (tmp_path / "ev", tmp_path / "engine", tmp_path / "dl")
    p = RC.CommentStore(tmp_path / "data", protected=roots).save(c)
    assert json.loads(p.read_text(encoding="utf-8"))["text"] == FELIPE
    assert RC.CommentStore(tmp_path / "data", protected=roots).list(PLANT, ROOM)[0]["id"] == c.id
    with pytest.raises(E.EvidenceError):
        RC.CommentStore(tmp_path / "engine", protected=roots).save(c)


# ------------------------------------------------------------------------------------- janela: passo 10
@pytest.fixture
def studio(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    ev, engine, dl = build_all(tmp_path)
    (engine / ".git").mkdir()                                                    # o motor é um checkout
    monkeypatch.setenv("AGENT_BAR_ENGINE_DIR", str(engine))
    stub = Path(__file__).with_name("fake_acp_agent.py")
    win = CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                        workspaces_fn=lambda: [tmp_path / "x"],
                        review_kwargs={"root_fn": lambda: ev, "load_fn": lambda r: E.load_catalog(r, expected={}),
                                       "gallery_fn": lambda cat: G.build_gallery(cat, engine, dl, rooms=[(PLANT, ROOM)]),
                                       "comment_store": RC.CommentStore(tmp_path / "data")})
    try:
        win.tabs.setCurrentWidget(win.review)
        win.review.reload()
        assert wait(lambda: win.review.design.history.count() > 0)
        yield win, engine, tmp_path
    finally:
        win.shutdown()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_step10_comment_opens_the_chat_prefilled_with_context_and_does_not_send(studio):
    win, engine, tmp = studio
    d = win.review.design
    d.history.setCurrentRow(0)
    d.compare_with_previous()                                                   # o Felipe está comparando as versões
    d.comment.setText(FELIPE)
    d.comment_btn.click()
    assert wait(lambda: win.tabs.currentWidget() is win.sessions)
    text = win.chat.input.toPlainText()
    assert text == RC.chat_request(win.review.last_comment)
    for piece in (FELIPE, f"run visível: {UNPUB_FAIL}", "câmera: dia_cama_3q", f"comparado com a versão anterior: {BAD_SHA}",
                  "referência do cômodo: ref-quarto-r2@v1", "R41, sem triagem"):
        assert piece in text, piece
    assert not RC.mentions_files(text)                                           # contexto, não nome de arquivo
    assert win.chat.workspace() == str(engine)                                    # no checkout do motor
    t_end = time.monotonic() + 1.5
    while time.monotonic() < t_end:                                               # dá tempo de um envio indevido
        _APP.processEvents()
        time.sleep(0.02)
    assert win.chat.chat.messages == [] and win.chat.input.toPlainText() == text  # nada foi enviado sozinho
    [saved] = win.review.comment_store.list(PLANT, ROOM)
    assert (saved["run"]["run_id"], saved["camera"], saved["compared_with"]["run_id"]) == (UNPUB_FAIL, "dia_cama_3q",
                                                                                          BAD_SHA)
    assert saved["text"] == FELIPE and saved["acao"] == "" and saved["author"].startswith("human")
    assert d.comment.text() == "" and "registrado (R41, sem triagem)" in d.comment_status.text()


def test_comment_without_a_selected_render_does_not_open_the_chat(studio):
    win, _engine, _tmp = studio
    d = win.review.design
    d.history.setCurrentRow(-1)
    d.comment.setText(FELIPE)
    d.comment_btn.click()
    _APP.processEvents()
    assert win.tabs.currentWidget() is win.review and "escolha um render" in d.comment_status.text()
    assert win.review.comment_store.list(PLANT, ROOM) == []


def test_empty_comment_does_not_open_the_chat(studio):
    win, _engine, _tmp = studio
    d = win.review.design
    d.history.setCurrentRow(0)
    d.comment.setText("  ")
    d.comment_btn.click()
    _APP.processEvents()
    assert win.tabs.currentWidget() is win.review and "comentário vazio" in d.comment_status.text()
