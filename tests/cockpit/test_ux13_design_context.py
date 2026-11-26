"""UX-13 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9; WORKSPACE-UX F11): o contexto do Chat sai da seleção do Design
(planta ▸ cômodo ▸ câmera ▸ run), anexado SÓ por escolha. Critério: comentar no Design preenche o Chat sem enviar; o texto
anexado é o EXATO do preview. Sem rede; agente ACP de teste."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar.cockpit import design_context as DC  # noqa: E402
from agent_bar.cockpit import gallery as G  # noqa: E402
from agent_bar.cockpit.library import compose_prompt  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")
PLANT, ROOM = "planta_74", "suite_01"


def png(tmp_path: Path, name: str) -> Path:
    p = tmp_path / f"{name}.png"
    img = QImage(32, 18, QImage.Format.Format_RGB32)
    img.fill(0x223344)
    img.save(str(p))
    return p


def render(tmp_path, n: int, *keys: str) -> G.GalleryItem:
    run = f"202611{n:02d}-planta_74_suite_01-r{n}"
    imgs = [G.GalleryImage(k, png(tmp_path, f"{run}-{k}"), "0" * 64, "r", f"views/{k}.png") for k in keys]
    return G.GalleryItem(G.PUBLISHED, PLANT, ROOM, run, "render", started_at=f"2026-11-{n:02d}T10:00:00Z",
                         day=f"2026-11-{n:02d}", render_verdict="PASS",
                         build={"geometria": "PASS", "funcional": "PASS", "construção": "PASS"}, images=imgs)


def reference(tmp_path) -> G.GalleryItem:
    img = G.GalleryImage("r2", png(tmp_path, "ref"), "0" * 64, "dl", "refs/ref.png")
    return G.GalleryItem(G.REFERENCE, PLANT, ROOM, "ref-suite@v2", "reference", started_at="2026-11-05T00:00:00Z",
                         day="2026-11-05", images=[img], category="suite", title="ref-suite@v2")


def test_the_breadcrumb_and_the_context_text_are_deterministic(tmp_path):
    it = render(tmp_path, 14, "render_entardecer_r2_canto")
    img = it.images[0]
    ref = reference(tmp_path)
    assert DC.breadcrumb(it, img) == f"{PLANT} ▸ {ROOM} ▸ câmera r2_canto · luz entardecer ▸ run {it.item_id}"
    text = DC.context_text(it, img, reference=ref)
    assert text == DC.context_text(it, img, reference=ref)                         # mesma entrada, mesmo texto
    assert text.splitlines() == [
        "[Contexto do Design — anexado pelo Felipe]",
        f"planta: {PLANT}",
        f"cômodo: {ROOM}",
        "câmera: câmera r2_canto · luz entardecer (quadro render_entardecer_r2_canto)",
        f"run: {it.item_id} · {it.when()}",
        "referência vigente: ref-suite@v2 (categoria suite)",
    ]
    assert DC.context_text(None, None) == ""                                       # sem seleção: nada a anexar


def test_the_comparison_shown_is_part_of_the_context(tmp_path):
    a = render(tmp_path, 14, "render_entardecer_r2_canto")
    b = render(tmp_path, 10, "render_dia_r2_canto")
    text = DC.context_text(a, a.images[0], compared=(b, b.images[0]))
    assert text.splitlines()[-1] == f"comparando com: {b.item_id} · câmera r2_canto · luz dia"


@pytest.fixture
def win(tmp_path, monkeypatch):
    from agent_bar.cockpit.github import GhClient
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    (tmp_path / "ws").mkdir()
    w = CockpitWindow(gh=GhClient(exe=[sys.executable, str(Path(__file__).with_name("fake_gh.py"))]), repos=["o/r"],
                      profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                      workspaces_fn=lambda: [tmp_path / "ws"])
    items = sorted([render(tmp_path, 14, "render_entardecer_r2_canto"), render(tmp_path, 10, "render_dia_r2_canto")],
                   key=G.GalleryItem.sort_key, reverse=True) + [reference(tmp_path)]
    w.review.design.set_items(items)
    yield w
    from PySide6.QtCore import QCoreApplication, QEvent
    w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_nothing_is_attached_unless_felipe_chooses(win):
    d = win.review.design
    assert not d.attach_box.isChecked()                                            # padrão: desligado
    assert win.design_context() == ""
    assert d.attach_preview.isHidden()


def test_the_attached_text_is_exactly_the_preview_and_it_reaches_the_agent(win):
    d = win.review.design
    d.attach_box.setChecked(True)
    preview = d.attach_preview.toPlainText()
    assert preview.startswith("[Contexto do Design") and "câmera r2_canto" in preview
    assert d.breadcrumb.text().startswith(f"{PLANT} ▸ {ROOM} ▸ ")
    assert win.design_context() == preview                                         # o anexado é o do preview
    win.chat.send("ECHO-PROMPT melhora a luz")
    assert _wait(lambda: not win.chat.busy and any(m["role"] == "agent" for m in win.chat.chat.messages))
    got = next(m["text"] for m in win.chat.chat.messages if m["role"] == "agent")
    assert got == compose_prompt(preview, "ECHO-PROMPT melhora a luz")             # o agente recebeu exatamente isso
    from agent_bar.cockpit.ui.widgets import Notice
    notices = [n.text() for n in win.chat.transcript.findChildren(Notice)]
    assert any("Design" in t and str(len(preview)) in t for t in notices)           # o Chat diz o que anexou


def test_choosing_another_camera_changes_the_preview_before_sending(win):
    d = win.review.design
    d.attach_box.setChecked(True)
    before = d.attach_preview.toPlainText()
    d.history_toggle.setChecked(True)
    d.history.setCurrentRow(1)                                                      # o run mais antigo
    after = d.attach_preview.toPlainText()
    assert after != before and "luz dia" in after and win.design_context() == after


def test_commenting_fills_the_chat_without_sending(win):
    d = win.review.design
    d.comment.setText("quero o edredom mais natural")
    d.comment_btn.click()
    _APP.processEvents()
    assert win.current_page() is win.sessions
    assert "edredom mais natural" in win.chat.input.toPlainText()
    assert not win.chat.busy and not any(m["role"] == "user" for m in win.chat.chat.messages)   # nada foi enviado


def test_the_choice_is_consumed_by_the_send_so_a_second_send_carries_no_design_context(win):
    """CHANGES_REQUESTED do fmmodesto (#124 @ a102e56): uma seleção anexava o contexto a vários envios. A escolha vale
    para UM envio; o segundo vai sem contexto até o Felipe marcar de novo."""
    d = win.review.design
    d.attach_box.setChecked(True)
    preview = d.attach_preview.toPlainText()
    win.chat.send("ECHO-PROMPT primeiro")
    assert _wait(lambda: not win.chat.busy and sum(m["role"] == "agent" for m in win.chat.chat.messages) == 1)
    assert not d.attach_box.isChecked() and d.attach_preview.isHidden()           # consumida pelo envio
    win.chat.send("ECHO-PROMPT segundo")
    assert _wait(lambda: not win.chat.busy and sum(m["role"] == "agent" for m in win.chat.chat.messages) == 2)
    first, second = [m["text"] for m in win.chat.chat.messages if m["role"] == "agent"]
    assert first == compose_prompt(preview, "ECHO-PROMPT primeiro")
    assert second == "ECHO-PROMPT segundo"                                         # sem contexto do Design
