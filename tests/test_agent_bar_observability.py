"""FP-061: isolated Event Store fixture and the three views of the same Agent Bar."""
from __future__ import annotations

import sqlite3

import pytest

from agent_bar.observability import collect_observation
from tests.support.event_store import EventStore
from tests.test_agent_bar import gui, snap  # noqa: F401


def fixture_store(tmp_path):
    gov = tmp_path / "governance"
    gov.mkdir()
    path = gov / "event_store.db"
    store = EventStore(path, clock=lambda: 1_800_000_000.0)
    pr_id, _ = store.publish("PR_OPENED", "pr-17", "pr:17", {"pr": 17})
    task_id, _ = store.publish("REVIEW_REQUESTED", "review-17", "pr:17", {"pr": 17}, parent_event_id=pr_id)
    stdout = gov / "review.stdout.log"
    stdout.write_text("review started\nobserved result\n", encoding="utf-8")
    store.run_started(task_id, "codex", ["codex", "review"], gov, 4321, stdout, gov / "missing.stderr.log")
    return gov, path, task_id


def test_read_only_projection_uses_existing_store_and_bounded_log(tmp_path):
    gov, path, task_id = fixture_store(tmp_path)
    before = path.stat().st_mtime_ns
    o = collect_observation(gov, now=1_800_000_010.0)
    assert o.source == "lido" and o.counts["PENDING"] == 1
    assert o.tasks[0].event_id == task_id and o.tasks[0].parent_event_id is not None
    assert o.timeline[0].event_type == "REVIEW_REQUESTED"
    assert o.pipeline[0].job_id == "pr:17"
    assert "observed result" in o.terminal
    assert o.outbox_pending == 0 and o.completed_24h == 0
    assert "indisponíveis" in o.messages_status
    assert path.stat().st_mtime_ns == before


def test_missing_and_incompatible_store_never_create_or_claim_healthy(tmp_path):
    gov = tmp_path / "governance"
    gov.mkdir()
    assert collect_observation(gov).source == "indisponível"
    assert not (gov / "event_store.db").exists()
    with sqlite3.connect(gov / "event_store.db") as db:
        db.execute("CREATE TABLE events (id INTEGER)")
    o = collect_observation(gov)
    assert o.source == "indisponível" and "incompleto" in o.reason


def test_terminal_path_outside_fixture_root_is_rejected(tmp_path):
    gov, path, task_id = fixture_store(tmp_path)
    outside = tmp_path / "private.log"
    outside.write_text("SECRET OUTSIDE ROOT", encoding="utf-8")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE subprocess_runs SET stdout_path=? WHERE event_id=?", (str(outside), task_id))
    o = collect_observation(gov)
    assert not o.terminal and "SECRET" not in o.terminal_status


def test_demo_is_explicit_and_cannot_be_mistaken_for_real(tmp_path, monkeypatch):
    gov, _, _ = fixture_store(tmp_path)
    monkeypatch.setenv("AGENT_BAR_DEMO", "1")
    o = collect_observation(gov)
    assert o.source == "demonstração" and o.reason == "Fixture isolada"


def test_explicit_coordinator_log_fallback_is_labelled_and_bounded(tmp_path, monkeypatch):
    gov, path, _ = fixture_store(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE subprocess_runs SET stdout_path=NULL,stderr_path=NULL")
    log = tmp_path / "run.log"
    log.write_text('{"at":"2026-11-16T12:00:00Z","review":{"action":"REVIEW_BLOCKED","pr":17,"verdict":"NEEDS_HUMAN"}}\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_BAR_HARNESS_LOG", str(log))
    o = collect_observation(gov)
    assert "coordenador" in o.terminal_status
    assert any("REVIEW_BLOCKED" in line and "PR #17" in line for line in o.terminal)


def test_expanded_and_full_modes_reuse_the_same_panel(gui):
    bar, app, _ = gui
    bar.show()
    bar.toggle_details()
    assert bar.details.isVisible() and not bar.details.full_mode
    assert not bar.details.operations.isVisible()
    bar.details.mode_button.click()
    assert bar.details.full_mode and bar.details.width() == bar.details.FULL_WIDTH
    assert bar.details.operations.isVisible()
    bar.details.mode_button.click()
    assert not bar.details.full_mode and bar.details.width() == bar.details.WIDTH
    bar.details.close_panel()
    assert bar.isVisible() and not bar.details.isVisible()


def test_full_view_renders_observed_facts_and_unavailable_channels(gui):
    bar, app, tmp = gui
    gov, _, _ = fixture_store(tmp)
    s = snap(tmp)
    s.observation = collect_observation(gov)
    bar.on_snapshot(s)
    bar.toggle_details()
    bar.details.toggle_full_mode()
    cards = bar.details.operations.cards
    assert "REVIEW_REQUESTED" in cards["timeline"].text()
    assert "pr:17" in cards["pipeline"].text()
    assert "indisponíveis" in cards["messages"].text()
    assert "observed result" in bar.details.operations.terminal.toPlainText()
    assert "LIDO" in bar.details.overview.text() and "REAL" not in bar.details.overview.text()
    assert "lido não comprova produção" in bar.details.overview.text()


def test_observe_only_guards_legacy_control_actions(gui, monkeypatch):
    from agent_bar import controls as ctl

    bar, _, _ = gui
    monkeypatch.setenv("AGENT_BAR_OBSERVE_ONLY", "1")
    monkeypatch.setattr(ctl, "stop_listener", lambda **kw: pytest.fail("não pode parar"))
    monkeypatch.setattr(ctl, "restart_listener_dry_run", lambda **kw: pytest.fail("não pode reiniciar"))
    monkeypatch.setattr(bar, "confirm", lambda text: pytest.fail("não pode abrir confirmação"))
    bar.action_stop()
    assert "somente leitura" in bar.details.status.text()
    bar.action_restart()
    assert "somente leitura" in bar.details.status.text()


# -- fila de PRs da FP-060 no painel + botao "Dar andamento" (a barra ajuda a finalizar os PRs; NUNCA mergeia) --------------------------------------------
def _pr_node(n, head="a" * 40, review=None, checks="SUCCESS", draft=False, mergeable="MERGEABLE", review_oid=None):
    reviews = [] if review is None else [{"state": review, "commit": {"oid": review_oid or head}}]
    return {"number": n, "title": f"PR {n}", "url": f"https://github.com/GFCDOTA/sketchup-mcp/pull/{n}", "isDraft": draft, "mergeable": mergeable, "headRefOid": head,
            "reviews": {"nodes": reviews}, "commits": {"nodes": [{"commit": {"statusCheckRollup": None if checks is None else {"state": checks}}}]}}


def _prs(nodes, more=False, cursor=None):
    import json
    return json.dumps({"data": {"repository": {"pullRequests": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}})


def test_each_open_pr_is_classified_by_what_has_to_happen_next():
    from agent_bar import collectors as col
    nodes = [_pr_node(1, review="APPROVED"),                                              # aprovado no HEAD + CI verde: pronto para o dono mergear
             _pr_node(2, review="CHANGES_REQUESTED"),                                      # achado do revisor no HEAD atual: corrigir
             _pr_node(3, review="CHANGES_REQUESTED", head="b" * 40, review_oid="a" * 40),  # correcao empurrada depois do review: aguarda nova revisao
             _pr_node(4),                                                                  # nunca revisado
             _pr_node(5, draft=True), _pr_node(6, mergeable="CONFLICTING", review="CHANGES_REQUESTED"),
             _pr_node(7, checks="FAILURE", review="APPROVED"),                             # aprovado mas CI vermelho: nunca "pronto"
             _pr_node(8, review="APPROVED", checks="PENDING"),                             # aprovado, CI ainda rodando: aguarda
             _pr_node(9, review="APPROVED", review_oid="c" * 40)]                          # aprovacao de OUTRO commit: nao vale para o HEAD
    q = col.collect_pr_queue(lambda a: (0, _prs(nodes)))
    got = {r.number: r.state for r in q.rows}
    assert got == {1: "pronto", 2: "corrigir", 3: "aguarda", 4: "aguarda", 5: "rascunho", 6: "conflito", 7: "ci", 8: "aguarda", 9: "aguarda"}
    assert [r.number for r in q.rows][:2] == [1, 2] and q.error is None                   # o que pede acao vem primeiro
    assert q.counts() == {"pronto": 1, "corrigir": 1, "conflito": 1, "ci": 1, "aguarda": 4, "rascunho": 1}


def test_a_pr_without_any_check_rollup_is_never_ready_to_merge():
    from agent_bar import collectors as col
    q = col.collect_pr_queue(lambda a: (0, _prs([_pr_node(1, review="APPROVED", checks=None)])))
    assert q.rows[0].state == "aguarda"                                                    # sem prova de CI nao existe "pronto"


@pytest.mark.parametrize("reply", [(1, "gh: rate limit"), (0, "<html>"), (0, '{"errors":[{"message":"boom"}]}'), (0, '{"data": null}')])
def test_pr_queue_failure_is_an_explicit_error_never_an_empty_list(reply):
    from agent_bar import collectors as col
    q = col.collect_pr_queue(lambda a: reply)
    assert q.rows == [] and q.error and q.synced is None


def test_pr_queue_follows_pagination_and_refuses_an_incomplete_count():
    from agent_bar import collectors as col
    pages = [_prs([_pr_node(1)], more=True, cursor="C1"), _prs([_pr_node(2)])]
    assert [r.number for r in col.collect_pr_queue(lambda a: (0, pages.pop(0))).rows] == [1, 2]
    forever = col.collect_pr_queue(lambda a: (0, _prs([_pr_node(1)], more=True, cursor="C")), max_pages=2)
    assert forever.rows == [] and "incompleta" in forever.error


def test_panel_shows_the_pr_queue_with_links_and_what_needs_the_owner(gui):  # noqa: F811
    from agent_bar import collectors as col
    bar, app, tmp = gui
    s = snap(tmp)
    s.pr_queue = col.collect_pr_queue(lambda a: (0, _prs([_pr_node(1, review="APPROVED"), _pr_node(2, review="CHANGES_REQUESTED"), _pr_node(3)])))
    bar.on_snapshot(s)
    text = bar.details.queue.text()
    assert "3 abertos" in text and "1 pronto" in text and "para você mergear" in text and "1 a corrigir" in text
    assert 'href="https://github.com/GFCDOTA/sketchup-mcp/pull/1"' in text and "#1" in text
    assert bar.details.queue.openExternalLinks() is True


def test_panel_shows_the_queue_error_instead_of_a_zero(gui):  # noqa: F811
    from agent_bar import collectors as col
    bar, app, tmp = gui
    s = snap(tmp)
    s.pr_queue = col.collect_pr_queue(lambda a: (1, "gh: rate limit"))
    bar.on_snapshot(s)
    assert "indisponível" in bar.details.queue.text() and "rate limit" in bar.details.queue.text() and "0 abertos" not in bar.details.queue.text()


def _stream_program(*events, sleep=0.0):
    """Programa falso que imprime eventos `stream-json` do `claude -p` (e opcionalmente fica vivo): troca o `claude` real nos testes."""
    import json
    import sys
    from pathlib import Path
    body = "import sys,time,json\n" + "\n".join(f"print({json.dumps(json.dumps(e))}, flush=True)" for e in events) + f"\ntime.sleep({sleep})\n"
    return (sys.executable, ["-c", body], Path.cwd())


def test_stream_lines_become_readable_terminal_lines():
    import json

    from agent_bar import controls as ctl
    f = ctl.format_stream_line
    assert f(json.dumps({"type": "system", "subtype": "init"})) == ["== sessao iniciada =="]
    assert f(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": " Panorama: 27 PRs "}]}})) == ["Panorama: 27 PRs"]
    tool = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "gh  pr list\n--state open"}}]}}
    assert f(json.dumps(tool)) == ["  > Bash: gh pr list --state open"]
    res = f(json.dumps({"type": "result", "is_error": False, "duration_ms": 3400, "num_turns": 2, "result": "feito"}))
    assert res[0].startswith("== FIM (ok, 3s") and res[1] == "feito"
    assert f(json.dumps({"type": "result", "is_error": True, "duration_ms": 1000}))[0].startswith("== FIM (ERRO")
    assert f("linha que nao e json") == ["linha que nao e json"] and f("") == [] and f("   ") == []
    assert f(json.dumps({"type": "user", "message": {}})) == []


def test_progress_command_runs_claude_headless_in_auto_mode_from_the_workspace(tmp_path, monkeypatch):
    from agent_bar import controls as ctl
    mission = tmp_path / "minha-missao.md"
    mission.write_text("# missao\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_BAR_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("AGENT_BAR_MISSION", str(mission))
    prog, args, cwd = ctl.progress_command(which=lambda name: "C:/fake/npm/claude.CMD")
    assert prog.lower().endswith(("claude.cmd", "claude.exe"))
    assert args[0] == "-p" and "minha-missao.md" in args[1]
    assert args[args.index("--permission-mode") + 1] == "auto" and args[args.index("--output-format") + 1] == "stream-json" and "--verbose" in args
    assert cwd == tmp_path
    assert ctl.progress_command(which=lambda name: None) is None                          # sem CLI: recusa
    monkeypatch.setenv("AGENT_BAR_MISSION", str(tmp_path / "nao-existe.md"))
    assert ctl.progress_command(which=lambda name: "x") is None                           # sem missao: recusa


def test_workspace_root_is_found_by_ops_launchers_or_the_environment(tmp_path, monkeypatch):
    from agent_bar import controls as ctl
    monkeypatch.setenv("AGENT_BAR_WORKSPACE", str(tmp_path))
    assert ctl.workspace_root() == tmp_path
    monkeypatch.delenv("AGENT_BAR_WORKSPACE")
    root = ctl.workspace_root()
    assert root.is_dir()                                                                   # dentro do workspace acha `ops/launchers`; fora dele cai no diretorio atual


def test_step_running_detects_a_claude_already_running_the_mission():
    from agent_bar import collectors as col
    from agent_bar import controls as ctl
    mission = col.Proc(95, 1, "claude.exe", "claude.exe -p Leia E:/Claude/ops/launchers/fp060-passo.md e execute", "2026-11-04T11:00:00Z")
    other = col.Proc(92, 1, "claude.exe", "claude.exe --permission-mode auto", "2026-11-04T11:00:00Z")
    assert ctl.step_running(lambda: [mission, other]) is True
    assert ctl.step_running(lambda: [other]) is False
    assert ctl.step_running(lambda: None) is False


@pytest.fixture()
def no_external_step(monkeypatch):
    from agent_bar import controls as ctl
    monkeypatch.setattr(ctl, "step_running", lambda *a, **k: False)


def test_progress_button_runs_the_step_inside_the_panel_and_streams_the_output(gui, monkeypatch, no_external_step):  # noqa: F811
    from agent_bar import controls as ctl
    from tests.test_agent_bar import wait_for
    bar, app, tmp = gui
    monkeypatch.setattr(ctl, "progress_command", lambda **kw: _stream_program(
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Panorama: 27 PRs abertos"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "gh pr list"}}]}},
        {"type": "result", "is_error": False, "duration_ms": 1000, "num_turns": 1, "result": "Passo concluido"}))
    asked = []
    monkeypatch.setattr(bar, "confirm", lambda text: asked.append(text) or True)
    bar.details.buttons["progress"].click()
    assert "merge" in asked[0].lower() and bar.step_process is not None
    assert wait_for(app, lambda: "passo encerrado" in bar.details.terminal.toPlainText(), secs=20)
    text = bar.details.terminal.toPlainText()
    assert "Panorama: 27 PRs abertos" in text and "> Bash: gh pr list" in text and "Passo concluido" in text
    assert bar.step_process is None and bar.details.buttons["progress"].text() == "Dar andamento (Claude)"
    assert not bar.details.terminal.isHidden() and not bar.details.terminal_title.isHidden()


def test_a_declined_confirmation_starts_nothing(gui, monkeypatch, no_external_step):  # noqa: F811
    from agent_bar import controls as ctl
    bar, app, tmp = gui
    monkeypatch.setattr(ctl, "progress_command", lambda **kw: pytest.fail("nao deve nem montar o comando"))
    monkeypatch.setattr(bar, "confirm", lambda text: False)
    bar.details.buttons["progress"].click()
    assert bar.step_process is None and bar.details.terminal.isHidden()


def test_the_stop_button_ends_only_the_step_it_started(gui, monkeypatch, no_external_step):  # noqa: F811
    from agent_bar import controls as ctl
    from tests.test_agent_bar import wait_for
    bar, app, tmp = gui
    monkeypatch.setattr(ctl, "progress_command", lambda **kw: _stream_program({"type": "system", "subtype": "init"}, sleep=60))
    monkeypatch.setattr(bar, "confirm", lambda text: True)
    bar.details.buttons["progress"].click()
    assert wait_for(app, lambda: "sessao iniciada" in bar.details.terminal.toPlainText(), secs=20)
    assert bar.details.buttons["progress"].text() == "Parar passo"
    pid = int(bar.step_process.processId())
    bar.details.buttons["progress"].click()
    assert wait_for(app, lambda: bar.step_process is None, secs=20)
    assert "passo interrompido" in bar.details.terminal.toPlainText()
    assert bar.details.buttons["progress"].text() == "Dar andamento (Claude)"
    import subprocess
    alive = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    assert str(pid) not in alive


def test_a_step_already_running_in_another_window_blocks_a_second_one(gui, monkeypatch):  # noqa: F811
    from agent_bar import controls as ctl
    bar, app, tmp = gui
    monkeypatch.setattr(ctl, "step_running", lambda *a, **k: True)
    monkeypatch.setattr(ctl, "progress_command", lambda **kw: pytest.fail("nao pode iniciar um 2o passo"))
    monkeypatch.setattr(bar, "confirm", lambda text: pytest.fail("nem pergunta"))
    bar.details.buttons["progress"].click()
    assert bar.step_process is None and "outra janela" in bar.details.status.text()


def test_a_missing_claude_cli_is_reported_not_faked(gui, monkeypatch, no_external_step):  # noqa: F811
    from agent_bar import controls as ctl
    bar, app, tmp = gui
    monkeypatch.setattr(ctl, "progress_command", lambda **kw: None)
    monkeypatch.setattr(bar, "confirm", lambda text: True)
    bar.details.buttons["progress"].click()
    assert bar.step_process is None and "não encontrado" in bar.details.status.text()


def test_progress_button_is_disabled_in_the_read_only_instance(gui, monkeypatch):  # noqa: F811
    from agent_bar import controls as ctl
    bar, app, tmp = gui
    monkeypatch.setenv("AGENT_BAR_OBSERVE_ONLY", "1")
    monkeypatch.setattr(ctl, "progress_command", lambda **kw: pytest.fail("instancia somente leitura nao abre sessao"))
    monkeypatch.setattr(bar, "confirm", lambda text: pytest.fail("nem pergunta"))
    bar.action_progress()
    assert "somente leitura" in bar.details.status.text()


# -- icones: bolinha colorida quando conectado/desconectado; logo animado do proprio agente quando trabalhando; botao play tambem na barra --------------------------------
def _snap_with(tmp, **levels):
    from agent_bar.models import AgentStatus, Level
    s = snap(tmp)
    for name, (level, label) in levels.items():
        setattr(s, name, AgentStatus({"gpt": "GPT", "github": "GitHub"}.get(name, name.capitalize()), level, label, "detalhe"))
    return s


def test_idle_agents_show_a_colored_dot_and_working_agents_show_their_animated_logo(gui):  # noqa: F811
    from agent_bar.models import Level
    bar, app, tmp = gui
    s = _snap_with(tmp, claude=(Level.BLUE, "Passo FP-060 (botao)"), codex=(Level.GREEN, "Conectado"), gpt=(Level.GRAY, "Indisponivel"),
                   github=(Level.GREEN, "Conectado"))
    s.listener = __import__("dataclasses").replace(s.listener, name="Listener", level=Level.GRAY, label="Desligado")
    bar.on_snapshot(s)
    assert bar.icons["Claude"].working is True and bar.icons["Claude"].level is Level.BLUE
    for name in ("Codex", "GPT", "Listener", "GitHub"):
        assert bar.icons[name].working is False, name
    assert bar.icons["Codex"].level is Level.GREEN and bar.icons["GPT"].level is Level.GRAY
    assert bar.anim_timer.isActive()                                                      # animando enquanto alguem trabalha
    from dataclasses import replace

    from agent_bar.models import AgentStatus
    bar.on_snapshot(replace(s, claude=AgentStatus("Claude", Level.GREEN, "Conectado", "detalhe")))
    assert not any(i.working for i in bar.icons.values()) and not bar.anim_timer.isActive()   # ninguem trabalhando: sem animacao (nao gasta CPU)


def test_each_agent_has_its_own_logo_painter_and_the_animation_advances(gui):  # noqa: F811
    from agent_bar import window as w
    bar, app, tmp = gui
    assert set(w.LOGO_PAINTERS) == {"Claude", "Codex", "GPT", "Listener", "GitHub"}
    icon = bar.icons["Claude"]
    first = icon.phase
    bar._tick_animation()
    assert bar.icons["Claude"].phase != first or not icon.working                          # a fase so avanca para quem trabalha
    from agent_bar.models import Level
    icon.set_state(Level.BLUE, True)
    before = icon.phase
    bar._tick_animation()
    assert icon.phase != before
    pix = icon.grab()                                                                      # os desenhos nao quebram em nenhum estado
    assert not pix.isNull()
    for name, ic in bar.icons.items():
        ic.set_state(Level.BLUE, True)
        assert not ic.grab().isNull(), name


def test_the_bar_has_a_play_button_that_runs_the_same_step_and_turns_into_stop(gui, monkeypatch, no_external_step):  # noqa: F811
    from agent_bar import controls as ctl
    from tests.test_agent_bar import wait_for
    bar, app, tmp = gui
    assert bar.play.text() == "▶" and "andamento" in bar.play.toolTip().lower()
    asked = []
    monkeypatch.setattr(bar, "confirm", lambda text: asked.append(text) or False)
    bar.play.click()
    assert len(asked) == 1 and bar.step_process is None                                    # o MESMO fluxo do botao do painel (confirmacao primeiro)
    monkeypatch.setattr(bar, "confirm", lambda text: True)
    monkeypatch.setattr(ctl, "progress_command", lambda **kw: _stream_program({"type": "system", "subtype": "init"}, sleep=60))
    bar.play.click()
    assert wait_for(app, lambda: "sessao iniciada" in bar.details.terminal.toPlainText(), secs=20)
    assert bar.play.text() == "■" and bar.details.buttons["progress"].text() == "Parar passo"       # os dois lugares ficam em sincronia
    bar.play.click()
    assert wait_for(app, lambda: bar.step_process is None, secs=20)
    assert bar.play.text() == "▶" and bar.details.buttons["progress"].text() == "Dar andamento (Claude)"


def test_the_play_button_is_disabled_in_the_read_only_instance(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    monkeypatch.setenv("AGENT_BAR_OBSERVE_ONLY", "1")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from agent_bar import settings as cfg
    from agent_bar import window as w
    app = QApplication.instance() or QApplication([])
    (tmp_path / "event_bridge.log").write_text("{}\n", encoding="utf-8")
    bar = w.Bar(cfg.Settings(), gov=tmp_path)
    bar.worker.stop()
    assert not bar.play.isEnabled()
    bar.details.close()
    app.processEvents()


def test_the_panel_explains_the_colors_and_marks_working_agents_with_a_glyph(gui):  # noqa: F811
    from agent_bar.models import Level
    bar, app, tmp = gui
    bar.on_snapshot(_snap_with(tmp, claude=(Level.BLUE, "Passo FP-060 (botao)")))
    legend = bar.details.legend.text().lower()
    assert "conectado" in legend and "desconectado" in legend and "logo animado" in legend and "trabalhando" in legend
    assert "&#10043;" in bar.details.agents.text()                                         # glifo de trabalho no lugar da bolinha azul


def test_pr_links_are_rendered_only_for_github_urls():
    from agent_bar import window as w
    from agent_bar.models import PrQueue, PrRow
    q = PrQueue(rows=[PrRow(1, "ok", "https://github.com/o/r/pull/1", "corrigir"), PrRow(2, "mau", "javascript:alert(1)", "corrigir"),
                      PrRow(3, "file", "file:///C:/Windows/System32/calc.exe", "ci")], synced="2026-11-04T12:00:00Z")
    html = w.queue_html(q)
    assert 'href="https://github.com/o/r/pull/1"' in html
    assert "javascript:" not in html and "file:///" not in html                              # nunca vira link clicavel
    assert "#2" in html and "#3" in html                                                     # mas o PR continua listado, como texto
