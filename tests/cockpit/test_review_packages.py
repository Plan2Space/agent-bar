"""F7.4: pacotes de correção (Claude) e GPT — conteúdo obrigatório, referência com origem, fora do evidence, run novo."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import evidence as E
from agent_bar.cockpit import review_packages as RP
from agent_bar.cockpit import review_report as RR
from tests.cockpit.evidence_fixture import build


@pytest.fixture
def env(tmp_path):
    root = build(tmp_path / "ev")
    cat = E.load_catalog(root, expected={})
    by = {r.stage: r for r in cat.runs}
    a, b = by["A"], by["B"]
    rep = RR.DiffReport.new(a, b)
    src_path, src_sha = RR.measure_sources(b)[0]
    rep.add(RR.Finding("material", "estetico", "tecido sem textura", "top"))
    rep.add(RR.Finding("geometria_medida", "fisico", "folga lateral 0,55 m", "iso_sw",
                       measure=RR.Measure(0.55, "m", src_path, src_sha, "B")))
    writer = RP.PackageWriter(tmp_path / "data", evidence_root=root, clock=lambda: time.mktime((2026, 11, 22, 12, 0, 0, 0, 0, -1)))
    return root, a, b, by["C"], rep, writer


def ref(r, name):
    return next(p for p in r.references() if p.name == name)


def test_claude_package_has_every_required_part_and_demands_a_new_run(env):
    root, a, b, _, rep, w = env
    out = w.claude(b, rep, ["top", "iso_sw"], subject="Cama sem textura", reference=ref(b, "briefing.md"), compared_with=a,
                   extra_criteria=["tecido com relevo visível na câmera top"])
    data = json.loads((out / "request.json").read_text(encoding="utf-8"))
    assert out.name == "2026-11-22-cama-sem-textura" and not E.is_inside(out, root)
    assert data["origin_run"]["run_id"] == "run-b" and len(data["origin_run"]["manifest_sha256"]) == 64
    assert data["origin_run"]["engine_commit"] == "c" * 40 and data["compared_with"]["stage"] == "A"
    assert [i["camera"] for i in data["images"]] == ["top", "iso_sw"] and all(len(i["sha256"]) == 64 for i in data["images"])
    assert data["reference"]["status"] == "aprovada" and "PDF do Felipe" in data["reference"]["origin"]
    assert [p["nature"] for p in data["problems"]] == ["estético", "físico"]
    assert data["problems"][1]["measure"]["sha256"] == RR.measure_sources(b)[0][1]
    assert data["constraints"]["fixture"]["path"] == "fx.json"
    assert data["must_create_new_run"] is True and data["agent_may_approve_visually"] is False
    assert any("RUN NOVO" in c for c in data["acceptance_criteria"]) and "tecido com relevo visível na câmera top" in data["acceptance_criteria"]
    md = (out / "request.md").read_text(encoding="utf-8")
    assert "run novo" in md and "PENDING_HUMAN" in md and "Critérios de aceite" in md


def test_reference_without_origin_is_refused(env):
    _, a, b, _, rep, w = env
    with pytest.raises(RP.PackageError, match="sem origem"):
        w.claude(b, rep, ["top"], subject="x", reference=ref(b, "sem_origem.md"))
    with pytest.raises(RP.PackageError, match="sem origem"):
        w.gpt(b, rep, ["top"], subject="x", briefing="b", question="q?", references=[ref(b, "sem_origem.md")])


def test_without_reference_says_so_explicitly(env):
    _, a, b, _, rep, w = env
    data = json.loads((w.claude(b, rep, ["top"], subject="x") / "request.json").read_text(encoding="utf-8"))
    assert data["reference"] == {"status": "sem referência aprovada"}


def test_claude_package_requires_problems_and_a_valid_run(env):
    _, a, b, c, rep, w = env
    with pytest.raises(RP.PackageError, match="relatório"):
        w.claude(b, RR.DiffReport.new(a, b), ["top"], subject="x")
    with pytest.raises(RP.PackageError, match="inválido ou ausente"):
        w.claude(c, rep, ["top"], subject="x")


def test_packages_never_overwrite_each_other(env):
    _, a, b, _, rep, w = env
    first = w.claude(b, rep, ["top"], subject="mesmo assunto")
    second = w.claude(b, rep, ["top"], subject="mesmo assunto")
    assert first != second and second.name.endswith("-2") and (first / "request.json").is_file()


def test_writing_a_package_inside_the_evidence_is_refused(env):
    root, a, b, _, rep, _ = env
    bad = RP.PackageWriter(root / "planta_x" / "quarto" / "B" / "run-b", evidence_root=root)
    with pytest.raises(E.EvidenceError, match="somente leitura"):
        bad.claude(b, rep, ["top"], subject="x")
    with pytest.raises(E.EvidenceError):
        bad.gpt(b, rep, ["top"], subject="x", briefing="b", question="q?")


def test_gpt_package_layout_content_and_copied_images(env):
    root, a, b, _, rep, w = env
    out = w.gpt(b, rep, ["top", "iso_sw"], subject="Cabeceira", briefing="Cabeceira estofada, sem trono.",
                question="Qual proporção de cabeceira combina com 2,60 m de pé-direito?", references=[ref(b, "briefing.md")])
    assert out.relative_to(w.root).as_posix() == "requests/gpt/planta_x/quarto/B/run-b/2026-11-22-cabeceira"
    data = json.loads((out / "pacote.json").read_text(encoding="utf-8"))
    assert {i["camera"] for i in data["images"]} == {"top", "iso_sw", "pdf_x_skp"}           # câmeras + prancha (planta)
    for i in data["images"]:
        assert (out / i["file"]).is_file() and not E.is_inside(out / i["file"], root)
    assert data["question"].endswith("?") and data["briefing"] and data["approved_references"][0]["origin"]
    assert data["measurements"][1]["measure"]["unit"] == "m"
    assert "Não altera nenhum veredito" in data["how_to_use"]
    assert "Pergunta objetiva" in (out / "briefing.md").read_text(encoding="utf-8")


def test_gpt_package_requires_question_and_briefing(env):
    _, a, b, _, rep, w = env
    with pytest.raises(RP.PackageError, match="pergunta"):
        w.gpt(b, rep, ["top"], subject="x", briefing="b", question="  ")
    with pytest.raises(RP.PackageError, match="briefing"):
        w.gpt(b, rep, ["top"], subject="x", briefing="", question="q?")


def test_evidence_checkout_is_untouched_by_packages(env):
    root, a, b, _, rep, w = env
    before = {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*")}
    w.claude(b, rep, ["top"], subject="x")
    w.gpt(b, rep, ["top"], subject="y", briefing="b", question="q?")
    assert {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*")} == before


# --------------------------------------------------------------------------------------------------------- GUI


def test_panel_generates_and_hands_the_request_to_chat_without_sending(env, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit.ui.packages_panel import PackagesPanel
    root, a, b, _, rep, w = env
    eng = tmp_path / "motor"
    (eng / ".git").mkdir(parents=True)
    panel = PackagesPanel(w, engine_workspace=eng)
    panel.report_fn = lambda: rep
    got = []
    panel.send_to_chat.connect(lambda ws, text: got.append((ws, text)))
    try:
        panel.set_pair(a, b, ["top", "iso_sw"])
        labels = [panel.reference.itemText(i) for i in range(panel.reference.count())]
        assert any("SEM ORIGEM" in t for t in labels) and any("PDF do Felipe" in t for t in labels)
        panel.reference.setCurrentIndex(next(i for i, t in enumerate(labels) if "SEM ORIGEM" in t))
        assert panel.make_claude() is None and "sem origem" in panel.status.text()
        panel.reference.setCurrentIndex(0)
        panel.subject.setText("tecido")
        out = panel.make_claude()
        assert out is not None and panel.chat_btn.isEnabled()
        panel.open_in_chat()
        assert got and got[0][0] == str(eng) and "Pedido de correção" in got[0][1]
        assert panel.make_gpt() is None and "pergunta" in panel.status.text()
    finally:
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_window_puts_the_request_in_the_chat_and_does_not_send(env, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    stub = Path(__file__).with_name("fake_acp_agent.py")
    ws = tmp_path / "motor"
    ws.mkdir()
    win = CockpitWindow(repos=["Plan2Space/agent-bar"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                        workspaces_fn=lambda: [tmp_path / "x"])
    try:
        win.open_correction(str(ws), "# Pedido de correção — teste")
        assert win.tabs.currentWidget() is win.sessions and win.chat.workspace() == str(ws)
        assert win.chat.input.toPlainText() == "# Pedido de correção — teste" and win.chat.chat.messages == []
    finally:
        win.shutdown()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
