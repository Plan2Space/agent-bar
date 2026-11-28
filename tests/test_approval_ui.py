"""S00-14 (UI): aprovacao humana presa ao HEAD exato. Risco e decisao vem do modulo `approval` do agent-runtime.

Todo GitHub aqui e FALSO (fake_gh). Nenhum teste posta comentario real. Repos/PRs `Plan2Space/teste-*` sao ficticios.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from agent_bar import approvals as ap

SHA1 = "a" * 40
SHA2 = "b" * 40
REPO = "Plan2Space/teste-ui"


def pr_state(head=SHA1, files=None, labels=(), comments=(), reviews=(), rollup="SUCCESS", title="feat: algo"):
    files = files if files is not None else [{"filename": "agent_bar/x.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}]
    return {"head": head, "files": list(files), "labels": list(labels), "comments": list(comments), "reviews": list(reviews),
            "rollup": rollup, "title": title}


def comment(body, login="fmodesto30", created="2026-11-21T10:00:00Z", updated=None, n=1):
    return {"user": {"login": login, "type": "User"}, "body": body, "created_at": created, "updated_at": updated or created,
            "html_url": f"https://github.com/{REPO}/pull/7#issuecomment-{n}"}


def fake_gh(prs, login="fmodesto30", posted=None, after_first_read=None):
    """`after_first_read`: estado do PR no momento do clique (a tela foi montada por outro fake, com o estado antigo)."""
    calls = {"reads": 0}

    def gh(args):
        if args[:2] == ["api", "user"]:
            return 0, login
        if args[:2] == ["api", "graphql"]:
            fields = dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("query="))
            key = (f"{fields['owner']}/{fields['name']}", int(fields["number"]))
            calls["reads"] += 1
            st = prs[key]
            if after_first_read:                                                   # este gh so e usado no clique: estado novo
                st = after_first_read
            node = {"headRefOid": st["head"], "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": st["rollup"]} if st["rollup"] else None}}]}}
            return 0, json.dumps({"data": {"repository": {"pullRequest": node}}})
        if args[0] == "api" and args[1].startswith("repos/"):
            parts = args[1].split("/")
            key = (f"{parts[1]}/{parts[2]}", int(parts[4]))
            st = prs[key]
            tail = parts[5] if len(parts) > 5 else ""
            if parts[3] == "pulls" and tail == "":
                return 0, json.dumps({"title": st["title"], "html_url": f"https://github.com/{key[0]}/pull/{key[1]}", "head": {"sha": st["head"]},
                                      "labels": [{"name": n} for n in st["labels"]], "draft": False})
            rows = {"files": st["files"], "comments": st["comments"], "reviews": st["reviews"]}[tail]
            return 0, "\n".join(json.dumps(r) for r in rows)
        if args[:2] == ["pr", "comment"]:
            if posted is None:
                pytest.fail("teste tentou postar comentario sem lista de captura")
            posted.append(args)
            return 0, f"https://github.com/{REPO}/pull/7#issuecomment-99"
        pytest.fail(f"chamada inesperada ao gh: {args}")
    return gh


def collect(prs, **kw):
    return ap.collect_approvals(fake_gh(prs, **{k: v for k, v in kw.items() if k in ("login",)}), list(prs),
                                approvers=kw.get("approvers", ("fmodesto30",)), runs=kw.get("runs"), store_path=kw.get("store_path"))


# ------------------------------------------------------------------------------------------------ risco e estado
def test_risk_comes_from_the_runtime_classifier_not_from_the_ui():
    from agent_runtime.approval import classify_risk
    prs = {(REPO, 7): pr_state(files=[{"filename": ".github/workflows/ci.yml", "status": "modified", "patch": "x"}])}
    row = collect(prs).rows[0]
    assert (row.risk, row.risk_reasons) == classify_risk([".github/workflows/ci.yml"], file_changes={".github/workflows/ci.yml": {"status": "modified", "patch": "x"}})
    assert row.risk == "HIGH" and row.state == "aguardando" and row.can_approve is True and row.head_sha == SHA1


@pytest.mark.parametrize("repo, path", [("Plan2Space/engineering-specs", "plan2space/P2S-X.md"),
                                        ("Plan2Space/agent-bar", "agent_bar/cockpit/devmode.py")])
def test_the_bar_classifies_with_the_pr_repo_profile_like_the_runtime_gate(repo, path):
    """Achado do revisor no #228: com o runtime ca323420 o classify_risk aceita repo= (perfil por repo, #69) e o
    merge_gate passa o repo. Sem o repo aqui, um SPEC do engineering-specs aparecia LOW na barra e HIGH no gate."""
    from agent_runtime.approval import classify_risk
    change = {"status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}
    prs = {(repo, 7): pr_state(files=[{"filename": path, **change}])}
    row = collect(prs).rows[0]
    gate = classify_risk([path], file_changes={path: change}, repo=repo)          # o que o merge_gate do runtime calcula
    assert gate[0] == "HIGH"
    assert (row.risk, row.risk_reasons) == gate


def test_low_risk_with_green_ci_needs_no_human():
    prs = {(REPO, 7): pr_state(files=[{"filename": "docs/a.md", "status": "modified", "patch": "x"}])}
    row = collect(prs).rows[0]
    assert row.risk == "LOW" and row.state == "auto_low" and row.can_approve is False and "LOW" in row.blocked_reason


def test_a_valid_comment_at_head_counts_as_approval():
    prs = {(REPO, 7): pr_state(comments=[comment(f"APROVADO MEDIUM {SHA1}")])}
    row = collect(prs).rows[0]
    assert row.risk == "MEDIUM" and row.state == "aprovado" and row.can_approve is False and row.approval.approver == "fmodesto30"


def test_a_new_commit_invalidates_the_approval_and_the_row_goes_back_to_waiting():
    prs = {(REPO, 7): pr_state(head=SHA2, comments=[comment(f"APROVADO MEDIUM {SHA1}")])}
    row = collect(prs).rows[0]
    assert row.state == "aguardando" and row.can_approve is True and any("invalidated" in r for r in row.reasons)


def test_an_approval_with_a_lower_risk_does_not_cover_a_high_pr():
    files = [{"filename": "schemas/x.json", "status": "modified", "patch": "x"}]
    prs = {(REPO, 7): pr_state(files=files, comments=[comment(f"APROVADO MEDIUM {SHA1}")])}
    row = collect(prs).rows[0]
    assert row.risk == "HIGH" and row.state == "aguardando"


@pytest.mark.parametrize("c", [comment(f"APROVADO HIGH {SHA1}", login="outra-pessoa"),
                               comment(f"APROVADO HIGH {SHA1}", updated="2026-11-21T11:00:00Z"),
                               comment(f"APROVADO\tHIGH {SHA1}")])
def test_comments_outside_the_grammar_or_from_others_or_edited_never_approve(c):
    row = collect({(REPO, 7): pr_state(comments=[c])}).rows[0]
    assert row.state == "aguardando"


@pytest.mark.parametrize("rollup,word", [("FAILURE", "failure"), ("PENDING", "pending"), (None, "none")])
def test_ci_not_green_disables_the_approval_with_the_reason(rollup, word):
    row = collect({(REPO, 7): pr_state(rollup=rollup)}).rows[0]
    assert row.state == "bloqueado" and row.can_approve is False and word in row.blocked_reason


def test_engine_repo_without_a_run_at_head_is_missing_evidence(tmp_path):
    from agent_bar.runs import RunsView
    prs = {("Plan2Space/sketchup-mcp-next", 4): pr_state()}
    row = collect(prs, runs=RunsView("sem_dados", "x")).rows[0]
    assert row.evidence_ok is False and row.state == "bloqueado" and "sem evidência" in row.blocked_reason


def test_other_repos_say_run_evidence_does_not_apply_instead_of_green():
    row = collect({(REPO, 7): pr_state()}).rows[0]
    assert row.evidence_ok is None and "não se aplica" in row.evidence_note


def test_a_login_outside_the_approvers_cannot_approve():
    row = collect({(REPO, 7): pr_state()}, login="robo", approvers=("fmodesto30",)).rows[0]
    assert row.can_approve is False and "aprovadores" in row.blocked_reason


def test_without_configured_approvers_nobody_can_approve_and_it_says_so():
    board = collect({(REPO, 7): pr_state()}, approvers=())
    assert board.rows[0].can_approve is False and "AGENT_BAR_APPROVERS" in board.rows[0].blocked_reason


def test_every_board_carries_the_shared_credential_warning():
    board = collect({(REPO, 7): pr_state()})
    assert "fmodesto30" in board.credential_warning and "não prova" in board.credential_warning


def test_a_github_failure_is_an_error_row_never_approvable():
    def gh(args):
        return (0, "fmodesto30") if args[:2] == ["api", "user"] else (1, "HTTP 502")
    board = ap.collect_approvals(gh, [(REPO, 7)], approvers=("fmodesto30",))
    assert board.rows[0].state == "erro" and board.rows[0].can_approve is False and "502" in board.rows[0].blocked_reason


# ------------------------------------------------------------------------------------------------ EventStore (somente leitura)
def _store(path, records):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, job_id TEXT, payload TEXT, status TEXT)")
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('schema_version','1')")
    for r in records:
        db.execute("INSERT INTO events (event_type, job_id, payload, status) VALUES ('GITHUB_REVIEW_SUBMITTED', ?, ?, 'RECORDED')",
                   (r["pr"], json.dumps(r)))
    db.commit()
    db.close()


def test_approvals_persisted_by_the_runtime_are_read_without_writing(tmp_path):
    db = tmp_path / "store.db"
    rec = {"pr": f"{REPO}#7", "head_sha": SHA1, "risk": "MEDIUM", "approver": "fmodesto30", "approved_at": "2026-11-21T10:00:00Z",
           "source": f"https://github.com/{REPO}/pull/7#pullrequestreview-1", "kind": "review"}
    _store(db, [rec])
    before = db.read_bytes()
    row = collect({(REPO, 7): pr_state()}, store_path=db).rows[0]
    assert row.state == "aprovado" and row.approval.source.endswith("review-1") and db.read_bytes() == before


# ------------------------------------------------------------------------------------------------ acao de aprovar
def _row(prs, **kw):
    return collect(prs, **kw).rows[0]


def test_the_approval_text_is_exactly_the_runtime_grammar():
    from agent_runtime.approval import APPROVAL_LINE
    line = ap.approval_line("HIGH", SHA1)
    assert line == f"APROVADO HIGH {SHA1}" and APPROVAL_LINE.fullmatch(line)


def test_approve_rechecks_head_and_posts_one_exact_line():
    prs = {(REPO, 7): pr_state()}
    posted = []
    row = _row(prs)
    ok, msg = ap.approve(fake_gh(prs, posted=posted), row, confirm=lambda text: True)
    assert ok and "issuecomment-99" in msg
    assert posted == [["pr", "comment", "7", "--repo", REPO, "--body", f"APROVADO MEDIUM {SHA1}"]]


def test_a_head_that_moved_after_the_screen_is_refused_with_the_reason():
    prs = {(REPO, 7): pr_state()}
    posted = []
    row = _row(prs)
    ok, msg = ap.approve(fake_gh(prs, posted=posted, after_first_read=pr_state(head=SHA2)), row, confirm=lambda text: True)
    assert not ok and posted == [] and SHA2[:12] in msg and "HEAD mudou" in msg


def test_ci_that_turned_red_before_the_click_is_refused():
    prs = {(REPO, 7): pr_state()}
    posted = []
    ok, msg = ap.approve(fake_gh(prs, posted=posted, after_first_read=pr_state(rollup="FAILURE")), _row(prs), confirm=lambda t: True)
    assert not ok and posted == [] and "CI" in msg


def test_declining_the_confirmation_posts_nothing():
    prs = {(REPO, 7): pr_state()}
    posted = []
    seen = []
    ok, msg = ap.approve(fake_gh(prs, posted=posted), _row(prs), confirm=lambda text: seen.append(text) or False)
    assert not ok and posted == [] and "cancelad" in msg
    assert SHA1 in seen[0] and "fmodesto30" in seen[0] and f"APROVADO MEDIUM {SHA1}" in seen[0]


def test_a_row_that_cannot_be_approved_is_refused_before_any_dialog():
    prs = {(REPO, 7): pr_state(rollup="FAILURE")}
    ok, msg = ap.approve(fake_gh(prs, posted=[]), _row(prs), confirm=lambda t: pytest.fail("nao deveria perguntar"))
    assert not ok and "failure" in msg


def test_the_approval_module_never_merges_nor_writes_the_event_store():
    import inspect
    src = inspect.getsource(ap)
    for banned in ('"merge"', "'merge'", "INSERT", "UPDATE ", "DELETE ", "ApprovalStore(", ".record("):
        assert banned not in src
    assert "mode=ro" in src


# ------------------------------------------------------------------------------------------------ UI
from tests.test_agent_bar import gui, snap  # noqa: E402,F401


def _open_full(bar, s):
    bar.on_snapshot(s)
    bar.toggle_details()
    bar.details.toggle_full_mode()
    return bar.details.operations


def test_full_view_shows_pr_risk_head_ci_evidence_and_the_credential_warning(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = collect({(REPO, 7): pr_state(files=[{"filename": "schemas/x.json", "status": "modified", "patch": "x"}])})
    ops = _open_full(bar, s)
    text = ops.cards["approval"].text()
    assert "teste-ui#7" in text and "HIGH" in text and SHA1 in text and "aguardando" in text.lower() and "CI success" in text
    assert "não prova" in text
    assert ops.approve_button.isEnabled() and SHA1[:12] in ops.approve_button.text()


def test_full_view_disables_the_button_with_the_reason_when_ci_is_red(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = collect({(REPO, 7): pr_state(rollup="FAILURE")})
    ops = _open_full(bar, s)
    assert not ops.approve_button.isEnabled() and "failure" in ops.approve_reason.text()


def test_without_any_pr_awaiting_the_card_says_so(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = collect({(REPO, 7): pr_state(files=[{"filename": "docs/a.md", "status": "modified", "patch": "x"}])})
    ops = _open_full(bar, s)
    assert "nenhum PR aguardando" in ops.cards["approval"].text() and not ops.approve_button.isEnabled()


def test_observe_only_mode_never_enables_the_approve_button(gui, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENT_BAR_OBSERVE_ONLY", "1")
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = collect({(REPO, 7): pr_state()})
    ops = _open_full(bar, s)
    assert not ops.approve_button.isEnabled() and "somente leitura" in ops.approve_reason.text()


def test_window_button_declined_confirmation_posts_nothing(gui, monkeypatch):  # noqa: F811
    from agent_bar import collectors as col
    bar, app, tmp = gui
    prs = {(REPO, 7): pr_state()}
    posted = []
    monkeypatch.setattr(col, "run_gh", fake_gh(prs, posted=posted))
    s = snap(tmp)
    s.approvals = collect(prs)
    _open_full(bar, s)
    monkeypatch.setattr(bar, "confirm", lambda text: False)
    bar.details.operations.approve_button.click()
    app.processEvents()
    assert posted == [] and "cancelada" in bar.details.status.text()


def test_window_button_confirmed_rechecks_and_posts_the_exact_line(gui, monkeypatch):  # noqa: F811
    import time

    from agent_bar import collectors as col
    bar, app, tmp = gui
    prs = {(REPO, 7): pr_state()}
    posted = []
    monkeypatch.setattr(col, "run_gh", fake_gh(prs, posted=posted))
    s = snap(tmp)
    s.approvals = collect(prs)
    _open_full(bar, s)
    seen = []
    monkeypatch.setattr(bar, "confirm", lambda text: seen.append(text) or True)
    bar.details.operations.approve_button.click()
    end = time.time() + 5
    while time.time() < end and "postada" not in bar.details.status.text():
        app.processEvents()
        time.sleep(0.02)
    assert posted == [["pr", "comment", "7", "--repo", REPO, "--body", f"APROVADO MEDIUM {SHA1}"]]
    assert "postada" in bar.details.status.text() and "não prova" in seen[0]


# ------------------------------------------------------------------------------------------------ coleta em segundo plano
def test_background_cache_never_blocks_and_serves_the_last_value():
    from agent_bar.collectors import BackgroundCache
    pending, done = [], []
    clock = {"t": 0.0}
    c = BackgroundCache(ttl_s=60, clock=lambda: clock["t"], placeholder="coletando", spawn=pending.append, on_done=lambda: done.append(1))
    assert c.get("k", lambda: "v1") == "coletando" and len(pending) == 1          # 1a leitura: nao espera
    assert c.get("k", lambda: "v1") == "coletando" and len(pending) == 1          # ja tem uma em curso: nao dispara outra
    pending.pop()()
    assert c.get("k", lambda: "v2") == "v1" and done == [1] and pending == []     # dentro do TTL: valor pronto, sem nova coleta
    clock["t"] = 61
    assert c.get("k", lambda: "v2") == "v1" and len(pending) == 1                 # vencido: serve o anterior e atualiza em fundo
    pending.pop()()
    assert c.get("k", lambda: "v3") == "v2"


def test_a_failing_background_fetch_becomes_an_error_value_not_a_crash():
    from agent_bar.collectors import BackgroundCache
    pending = []
    c = BackgroundCache(ttl_s=60, placeholder="coletando", spawn=pending.append, on_error=lambda exc: f"erro: {exc}")
    c.get("k", lambda: (_ for _ in ()).throw(RuntimeError("gh caiu")))
    pending.pop()()
    assert c.get("k", lambda: "x") == "erro: gh caiu"


def test_while_collecting_the_card_says_so_instead_of_nothing_waiting(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = ap.ApprovalBoard(error="coletando aprovações (primeira leitura)")
    ops = _open_full(bar, s)
    text = ops.cards["approval"].text()
    assert "coletando" in text and "nenhum PR aguardando" not in text and not ops.approve_button.isEnabled()


def test_the_user_picks_which_waiting_pr_the_button_approves(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = collect({(REPO, 7): pr_state(), ("Plan2Space/teste-dois", 9): pr_state(head=SHA2)})
    ops = _open_full(bar, s)
    assert ops.approval_picker.count() == 2 and ops.selected_approval.number == 7
    ops.approval_picker.setCurrentIndex(1)
    assert ops.selected_approval.number == 9 and SHA2[:12] in ops.approve_button.text() and "teste-dois#9" in ops.approve_button.text()
    bar.on_snapshot(s)                                                                    # nova coleta mantem a escolha
    assert ops.selected_approval.number == 9
