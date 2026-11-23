"""EventStore (agent-runtime) lido em modo somente leitura, fila de PRs da org e as vistas da barra."""
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
    o = collect_observation(db_path=path, now=1_800_000_010.0)
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
    assert collect_observation(db_path=gov / "event_store.db").source == "indisponível"
    assert not (gov / "event_store.db").exists()
    with sqlite3.connect(gov / "event_store.db") as db:
        db.execute("CREATE TABLE events (id INTEGER)")
    o = collect_observation(db_path=gov / "event_store.db")
    assert o.source == "indisponível" and "incompleto" in o.reason


def test_without_configuration_nothing_is_read_from_any_legacy_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("EVENT_STORE_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs" / "governance").mkdir(parents=True)
    o = collect_observation()
    assert o.source == "indisponível" and "não configurado" in o.reason and o.db_path == ""


def test_the_configured_store_comes_from_the_environment(tmp_path, monkeypatch):
    gov, path, _ = fixture_store(tmp_path)
    monkeypatch.setenv("EVENT_STORE_DB_PATH", str(path))
    assert collect_observation().source == "lido"


def test_terminal_path_outside_fixture_root_is_rejected(tmp_path):
    gov, path, task_id = fixture_store(tmp_path)
    outside = tmp_path / "private.log"
    outside.write_text("SECRET OUTSIDE ROOT", encoding="utf-8")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE subprocess_runs SET stdout_path=? WHERE event_id=?", (str(outside), task_id))
    o = collect_observation(db_path=path)
    assert not o.terminal and "SECRET" not in o.terminal_status


def test_terminal_tail_redacts_credentials_before_reaching_the_panel(tmp_path):
    gov, path, task_id = fixture_store(tmp_path)
    token = "ghp_" + "a" * 36
    (gov / "review.stdout.log").write_text(
        f"push with {token}\nAuthorization: Bearer abcdefghijklmnop\napi_key=hunter2hunter2\nok line\n", encoding="utf-8")
    (gov / "missing.stderr.log").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nQUJDREVGR0hJSktM\n-----END RSA PRIVATE KEY-----\nboom\n",
        encoding="utf-8")
    text = "\n".join(collect_observation(db_path=path).terminal)
    assert "ok line" in text and "boom" in text
    for leaked in (token, "abcdefghijklmnop", "hunter2", "MIIEowIBAAKCAQEA", "QUJDREVGR0hJSktM"):
        assert leaked not in text
    assert "<segredo>" in text


def test_terminal_window_cut_inside_a_private_key_hides_the_key_body(tmp_path):
    gov, path, _ = fixture_store(tmp_path)
    body = "\n".join("QUJD" + str(i).zfill(4) * 15 for i in range(200))    # PEM body maior que a janela de 8 KiB
    (gov / "review.stdout.log").write_text(
        f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----\nafter key\n", encoding="utf-8")
    text = "\n".join(collect_observation(db_path=path).terminal)
    assert "after key" in text and "QUJD" not in text


def test_store_errors_and_details_are_redacted(tmp_path):
    gov, path, task_id = fixture_store(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE events SET error=? WHERE id=?", ("auth failed token=s3cr3tvalue", task_id))
        db.execute("UPDATE transitions SET detail=?", ("password: pa55word",))
    o = collect_observation(db_path=path)
    assert all("s3cr3tvalue" not in t.error for t in o.tasks)
    assert all("pa55word" not in e.detail for e in o.timeline)


def test_demo_is_explicit_and_cannot_be_mistaken_for_real(tmp_path, monkeypatch):
    gov, path, _ = fixture_store(tmp_path)
    monkeypatch.setenv("AGENT_BAR_DEMO", "1")
    o = collect_observation(db_path=path)
    assert o.source == "demonstração" and o.reason == "Fixture isolada"


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
    gov, path, _ = fixture_store(tmp)
    s = snap(tmp)
    s.observation = collect_observation(db_path=path)
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


# -- fila de PRs da org no painel (a barra so mostra; NUNCA mergeia) --------------------------------------------------------------------------------------
def _pr_node(n, head="a" * 40, review=None, checks="SUCCESS", draft=False, mergeable="MERGEABLE", review_oid=None):
    reviews = [] if review is None else [{"state": review, "commit": {"oid": review_oid or head}}]
    return {"number": n, "title": f"PR {n}", "url": f"https://github.com/Plan2Space/agent-bar/pull/{n}", "isDraft": draft, "mergeable": mergeable, "headRefOid": head,
            "repository": {"nameWithOwner": "Plan2Space/agent-bar"},
            "reviews": {"nodes": reviews}, "commits": {"nodes": [{"commit": {"statusCheckRollup": None if checks is None else {"state": checks}}}]}}


def _prs(nodes, more=False, cursor=None):
    import json
    return json.dumps({"data": {"search": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}})


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
    q = col.collect_org_pr_queue(lambda a: (0, _prs(nodes)))
    got = {r.number: r.state for r in q.rows}
    assert got == {1: "pronto", 2: "corrigir", 3: "aguarda", 4: "aguarda", 5: "rascunho", 6: "conflito", 7: "ci", 8: "aguarda", 9: "aguarda"}
    assert [r.number for r in q.rows][:2] == [1, 2] and q.error is None                   # o que pede acao vem primeiro
    assert q.counts() == {"pronto": 1, "corrigir": 1, "conflito": 1, "ci": 1, "aguarda": 4, "rascunho": 1}


def test_a_pr_without_any_check_rollup_is_never_ready_to_merge():
    from agent_bar import collectors as col
    q = col.collect_org_pr_queue(lambda a: (0, _prs([_pr_node(1, review="APPROVED", checks=None)])))
    assert q.rows[0].state == "aguarda"                                                    # sem prova de CI nao existe "pronto"


@pytest.mark.parametrize("reply", [(1, "gh: rate limit"), (0, "<html>"), (0, '{"errors":[{"message":"boom"}]}'), (0, '{"data": null}')])
def test_pr_queue_failure_is_an_explicit_error_never_an_empty_list(reply):
    from agent_bar import collectors as col
    q = col.collect_org_pr_queue(lambda a: reply)
    assert q.rows == [] and q.error and q.synced is None


def test_pr_queue_follows_pagination_and_refuses_an_incomplete_count():
    from agent_bar import collectors as col
    pages = [_prs([_pr_node(1)], more=True, cursor="C1"), _prs([_pr_node(2)])]
    assert [r.number for r in col.collect_org_pr_queue(lambda a: (0, pages.pop(0))).rows] == [1, 2]
    forever = col.collect_org_pr_queue(lambda a: (0, _prs([_pr_node(1)], more=True, cursor="C")), max_pages=2)
    assert forever.rows == [] and "incompleta" in forever.error


def test_panel_shows_the_pr_queue_with_links_and_what_needs_the_owner(gui):  # noqa: F811
    from agent_bar import collectors as col
    bar, app, tmp = gui
    s = snap(tmp)
    s.pr_queue = col.collect_org_pr_queue(lambda a: (0, _prs([_pr_node(1, review="APPROVED"), _pr_node(2, review="CHANGES_REQUESTED"), _pr_node(3)])))
    bar.on_snapshot(s)
    text = bar.details.queue.text()
    assert "3 abertos" in text and "1 pronto" in text and "para você mergear" in text and "1 a corrigir" in text
    assert 'href="https://github.com/Plan2Space/agent-bar/pull/1"' in text and "agent-bar#1" in text
    assert bar.details.queue.openExternalLinks() is True


def test_panel_shows_the_queue_error_instead_of_a_zero(gui):  # noqa: F811
    from agent_bar import collectors as col
    bar, app, tmp = gui
    s = snap(tmp)
    s.pr_queue = col.collect_org_pr_queue(lambda a: (1, "gh: rate limit"))
    bar.on_snapshot(s)
    assert "indisponível" in bar.details.queue.text() and "rate limit" in bar.details.queue.text() and "0 abertos" not in bar.details.queue.text()


# -- icones: bolinha colorida quando conectado/desconectado; logo animado do proprio agente quando trabalhando ------------------------------------------------------
def _snap_with(tmp, **levels):
    from agent_bar.models import AgentStatus, Level
    s = snap(tmp)
    for name, (level, label) in levels.items():
        setattr(s, name, AgentStatus({"github": "GitHub"}.get(name, name.capitalize()), level, label, "detalhe"))
    return s


def test_idle_agents_show_a_colored_dot_and_working_agents_show_their_animated_logo(gui):  # noqa: F811
    from agent_bar.models import Level
    bar, app, tmp = gui
    s = _snap_with(tmp, claude=(Level.BLUE, "Trabalhando"), github=(Level.GREEN, "Conectado"))
    bar.on_snapshot(s)
    assert bar.icons["Claude"].working is True and bar.icons["Claude"].level is Level.BLUE
    assert bar.icons["GitHub"].working is False and bar.icons["GitHub"].level is Level.GREEN
    assert bar.anim_timer.isActive()                                                      # animando enquanto alguem trabalha
    from dataclasses import replace

    from agent_bar.models import AgentStatus
    bar.on_snapshot(replace(s, claude=AgentStatus("Claude", Level.GREEN, "Conectado", "detalhe")))
    assert not any(i.working for i in bar.icons.values()) and not bar.anim_timer.isActive()   # ninguem trabalhando: sem animacao (nao gasta CPU)


def test_each_agent_has_its_own_logo_painter_and_the_animation_advances(gui):  # noqa: F811
    from agent_bar import window as w
    bar, app, tmp = gui
    assert set(w.LOGO_PAINTERS) == {"Claude", "GitHub"}
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


def test_the_panel_explains_the_colors_and_marks_working_agents_with_a_glyph(gui):  # noqa: F811
    from agent_bar.models import Level
    bar, app, tmp = gui
    bar.on_snapshot(_snap_with(tmp, claude=(Level.BLUE, "Trabalhando")))
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
