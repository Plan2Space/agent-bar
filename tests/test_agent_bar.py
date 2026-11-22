"""Coletores e acoes da barra (sem processo real). Honestidade: ausencia de dado nunca e verde.

O orquestrador legado (listener, laco do Codex, passo FP-060) e seus testes sairam em 2026-11-22
(docs/audit/legacy-cleanup-2026-11-22.md)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from agent_bar import collectors as col
from agent_bar import controls as ctl
from agent_bar import settings as cfg
from agent_bar.models import Level

NOW = dt.datetime(2026, 11, 4, 12, 0, 0, tzinfo=dt.timezone.utc)


def iso(sec_ago: int) -> str:
    return (NOW - dt.timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_github_cache_avoids_repeated_queries():
    calls = []
    t = [0.0]
    c = col.GithubCache(ttl_s=60, clock=lambda: t[0])

    def f():
        calls.append(1)
        return "v"
    c.get("k", f)
    c.get("k", f)
    assert len(calls) == 1
    t[0] = 61
    c.get("k", f)
    assert len(calls) == 2


# ------------------------------------------------------------------------------------------------------------- backlog
def gql(counts, more=False, cursor=None):
    nodes = [{"status": {"name": k}} for k, n in counts.items() for _ in range(n)]
    nodes.append({"status": None})                                                # item sem status
    return json.dumps({"data": {"organization": {"projectV2": {"items": {
        "pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}}})


def test_backlog_counts_come_from_the_project_and_missing_columns_are_zero():
    c, err, sync = col.collect_backlog(lambda a: (0, gql({"Ready": 2, "Blocked": 1})), now=NOW)
    assert err is None and c["Ready"] == 2 and c["Blocked"] == 1 and c["Done"] == 0 and sync == "2026-11-04T12:00:00Z"


def test_backlog_paginates_until_the_last_page():
    pages = [gql({"Ready": 2}, more=True, cursor="C1"), gql({"Ready": 3, "Done": 4})]
    calls = []

    def gh(args):
        calls.append(args)
        return 0, pages[len(calls) - 1]
    c, err, _ = col.collect_backlog(gh, now=NOW)
    assert err is None and c["Ready"] == 5 and c["Done"] == 4 and len(calls) == 2 and "after=C1" in calls[1]


def test_backlog_refuses_an_incomplete_count_instead_of_guessing():
    c, err, _ = col.collect_backlog(lambda a: (0, gql({"Ready": 1}, more=True, cursor="C")), now=NOW, max_pages=2)
    assert "incompleta" in err and all(v is None for v in c.values())


def test_backlog_error_is_specific_never_a_bare_indisponivel():
    c, err, sync = col.collect_backlog(lambda a: (1, "error: your token is missing required scopes [read:project]"), now=NOW)
    assert all(v is None for v in c.values()) and sync is None and "rc=1" in err and "read:project" in err and "auth refresh" in err
    c, err, _ = col.collect_backlog(lambda a: (1, "GraphQL: API rate limit exceeded for user ID 1"), now=NOW)
    assert "rate limit" in err
    c, err, _ = col.collect_backlog(lambda a: (0, "<html>"), now=NOW)
    assert "ilegivel" in err and all(v is None for v in c.values())
    c, err, _ = col.collect_backlog(lambda a: (0, json.dumps({"errors": [{"message": "Could not resolve to an Organization"}]})), now=NOW)
    assert "Could not resolve" in err


def test_snapshot_shows_the_backlog_error_and_never_the_last_good_numbers():
    # Mudou de proposito (pedido do Felipe, S00-13): falha na leitura do board = erro e indisponivel, sem numero antigo/cache.
    cache = col.GithubCache(ttl_s=0)
    ok = {"state": "good"}

    def gh(args):
        if args[:2] == ["api", "graphql"]:
            return (0, gql({"Ready": 7})) if ok["state"] == "good" else (1, "GraphQL: API rate limit exceeded")
        return 1, "offline"
    s1 = col.collect_snapshot(procs_fn=lambda: [], gh=gh, backlog_cache=cache, now=NOW)
    ok["state"] = "bad"
    s2 = col.collect_snapshot(procs_fn=lambda: [], gh=gh, backlog_cache=cache, now=NOW)
    assert s1.backlog["Ready"] == 7 and s1.backlog_error is None
    assert s2.backlog["Ready"] is None and "rate limit" in s2.backlog_error and s2.backlog_synced is None


def test_open_url_only_accepts_github():
    seen = []
    assert ctl.open_url("https://github.com/Plan2Space/agent-bar/pull/1", seen.append).ok and seen
    assert not ctl.open_url("http://evil.example/x", seen.append).ok and not ctl.open_url(None, seen.append).ok


def test_no_module_of_the_bar_can_commit_push_merge_or_kill_by_image_name():
    src = "".join(p.read_text(encoding="utf-8") for p in Path(ctl.__file__).parent.glob("*.py"))
    for forbidden in ("git push", "git commit", "taskkill /IM", "taskkill.exe /IM", "Stop-Process", "gh pr merge", "--execute"):
        assert forbidden not in src, forbidden


# ------------------------------------------------------------------------------------------------------------ settings
def test_settings_roundtrip_clamp_and_corrupt_file(tmp_path):
    p = tmp_path / "s.json"
    cfg.save(cfg.Settings(x=10, y=20, opacity=5, local_interval_s=0, github_interval_s=1), p)
    got = cfg.load(p)
    assert (got.x, got.y) == (10, 20) and got.opacity == 1.0 and got.local_interval_s == 2 and got.github_interval_s == 15
    p.write_text("{corrompido", encoding="utf-8")
    assert cfg.load(p) == cfg.Settings()


def test_gui_is_frameless_topmost_with_claude_and_github_and_a_cockpit_button(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from agent_bar import window as w
    app = QApplication.instance() or QApplication([])
    bar = w.Bar(cfg.Settings())
    try:
        f = bar.windowFlags()
        assert f & Qt.WindowType.FramelessWindowHint and f & Qt.WindowType.WindowStaysOnTopHint
        assert (bar.width(), bar.height()) == (w.BAR_W, 65)
        assert set(bar.labels) == {"Claude", "GitHub"} and set(w.LOGO_PAINTERS) == {"Claude", "GitHub"}
        assert bar.cockpit_btn.text() == "Cockpit" and not hasattr(bar, "play") and not hasattr(bar, "step_process")
    finally:
        bar.worker.stop()
        app.processEvents()


def test_every_subprocess_of_the_bar_is_created_without_a_console_window():
    import re
    for f in Path(ctl.__file__).parent.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.run\(", src):
            call = src[m.start():src.index("\n    ", m.start() + 40) if False else m.start() + 700]
            assert "creationflags=" in call.split("subprocess.run(", 2)[1][:700], f"{f.name}: subprocess.run sem creationflags"


# ---------------------------------------------------------------------------------------- botoes (GUI offscreen, um a um)
@pytest.fixture()
def gui(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from agent_bar import window as w
    app = QApplication.instance() or QApplication([])
    bar = w.Bar(cfg.Settings())
    bar.worker.snapshot.disconnect(bar.on_snapshot)                    # sem coleta de fundo: o teste injeta o Snapshot
    bar.worker.stop()
    yield bar, app, tmp_path
    bar.details.close()
    app.processEvents()


def status(bar):
    return bar.details.status.text()


def snap(tmp_path):
    return col.collect_snapshot(procs_fn=lambda: [], gh=lambda a: (1, "offline"), now=NOW)


def wait_for(app, cond, secs=5):
    import time
    end = time.time() + secs
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.02)
    return cond()


def test_button_repo_and_project_open_the_right_urls_and_report(gui, monkeypatch):
    bar, app, _ = gui
    opened = []
    monkeypatch.setattr(ctl.webbrowser, "open", lambda u: opened.append(u))
    bar.details.buttons["repo"].click()
    bar.details.buttons["project"].click()
    app.processEvents()
    assert opened == [ctl.REPO_URL, ctl.PROJECT_URL] and "\u2713" in status(bar) or "&#10003;" in status(bar)


def test_button_copy_diagnostic_really_copies_and_verifies(gui):
    bar, app, tmp = gui
    bar.details.buttons["diag"].click()
    assert "&#10007;" in status(bar) and "nada para copiar" in status(bar)           # sem coleta: nao finge sucesso
    bar.on_snapshot(snap(tmp))
    bar.details.buttons["diag"].click()
    from PySide6.QtWidgets import QApplication
    assert "&#10003;" in status(bar) and "P2S Studio snapshot" in QApplication.clipboard().text()


def test_button_refresh_confirms_only_when_a_new_snapshot_arrives(gui):
    bar, app, tmp = gui
    bar.details.buttons["refresh"].click()
    assert bar._refresh_pending is True and "&#10003;" not in status(bar)
    bar.on_snapshot(snap(tmp))
    assert "atualizado" in status(bar) and bar._refresh_pending is None
    bar._refresh_pending = True
    bar._refresh_timeout()
    assert "&#10007;" in status(bar) and "sem resposta" in status(bar)


# -- semantica unica das bolinhas: COR = conexao (verde conectado, cinza desconectado/sem integracao, ambar atencao, vermelho erro); TRABALHANDO = logo animado -----------------
def _claude(procs, tmp_path, login=None, installed=True):
    return col.collect_claude(procs, installed=lambda: installed, login=login)


def test_claude_logged_in_is_green_connected_even_without_sessions(tmp_path):
    s = _claude([], tmp_path, login=True)
    assert s.level is Level.GREEN and s.label == "Conectado" and "logado" in s.detail


def test_claude_logged_in_with_a_live_interactive_session_stays_green_and_names_the_session(tmp_path):
    inter = col.Proc(92, 1, "claude.exe", "C:/npm/claude.exe --permission-mode auto", iso(900))
    s = _claude([inter], tmp_path, login=True)
    assert s.level is Level.GREEN and "pid 92" in s.detail


def test_claude_without_login_is_red_and_not_installed_is_gray(tmp_path):
    s = _claude([], tmp_path, login=False)
    assert s.level is Level.RED and s.label == "Sem login" and "claude auth login" in s.detail
    assert _claude([], tmp_path, login=False, installed=False).level is Level.GRAY       # sem CLI nao ha o que logar


def test_an_unknown_login_is_never_green(tmp_path):
    assert _claude([], tmp_path, login=None).level is Level.GRAY
    inter = col.Proc(92, 1, "claude.exe", "C:/npm/claude.exe", iso(60))
    s = _claude([inter], tmp_path, login=None)
    assert s.level is Level.YELLOW and "login nao confirmado" in s.detail


def test_process_table_unavailable_is_gray_for_claude(tmp_path):
    s = _claude(None, tmp_path, login=True)
    assert s.level is Level.GRAY and s.label == "Indisponivel"


def test_desktop_helpers_and_interactive_sessions_are_named_but_never_working(tmp_path):
    procs = [col.Proc(90, 1, "claude.exe", '"C:/AnthropicClaude/app-4/claude.exe" --type=gpu-process', iso(900)),
             col.Proc(91, 1, "claude.exe", '"C:/AnthropicClaude/app-4/claude.exe" --type=renderer', iso(900)),
             col.Proc(92, 1, "claude.exe", "C:/Roaming/claude-code/2.1/claude.exe --output-format stream-json", iso(900))]
    st = _claude(procs, tmp_path, login=True)
    assert st.level is Level.GREEN and st.pid is None                                    # nenhuma sessao vira "trabalhando" por suposicao
    assert "1 sessao(oes) interativa(s) viva(s)" in st.detail and "2 processo(s) auxiliar(es)" in st.detail and "pid 92" in st.detail


# ---------------------------------------------------------------------------------------------------------------- github
def _queue(n=2, error=None):
    from agent_bar.models import PrQueue, PrRow
    return PrQueue(rows=[PrRow(i, f"pr {i}", f"https://github.com/Plan2Space/x/pull/{i}", "aguarda", "Plan2Space/x") for i in range(n)],
                   error=error)


def test_github_is_connected_only_when_a_real_read_of_this_collection_worked():
    asked = []
    st = col.collect_github_status(lambda a: asked.append(a) or (1, "nao deveria ser chamado"), _queue(3))
    assert st.level is Level.GREEN and "3 PR(s) abertos" in st.detail and asked == []   # leitura real prova o acesso


def test_github_without_any_read_explains_with_gh_auth_status():
    from agent_bar.models import PrQueue
    out = "github.com\n  X Failed to log in to github.com account fmodesto30 (keyring)\n  - The token in keyring is invalid."
    st = col.collect_github_status(lambda a: (1, out), PrQueue(error="rate limit"))
    assert st.level is Level.RED and st.label == "Sem acesso" and "Failed to log in" in st.detail
    assert col.collect_github_status(lambda a: (127, "nao encontrado"), PrQueue(error="x")).level is Level.GRAY
    st = col.collect_github_status(lambda a: (0, "Logged in"), PrQueue(error="GraphQL: boom"))
    assert st.level is Level.YELLOW and "GraphQL: boom" in st.detail


def test_snapshot_end_to_end_has_only_live_sources_and_no_secrets(monkeypatch):
    monkeypatch.setattr(col.shutil, "which", lambda name: f"C:/npm/{name}.cmd")        # CLI "instalada" sem depender do PATH da maquina (o CI nao tem claude)

    def gh(args):
        if args[:2] == ["api", "graphql"]:
            return 0, gql({"Ready": 5, "In Progress": 8, "Blocked": 3, "Done": 26})
        return 1, "offline"
    s = col.collect_snapshot(procs_fn=lambda: [col.Proc(92, 1, "claude.exe", "C:/npm/claude.exe ghp_" + "a" * 30, iso(60))], gh=gh,
                             now=NOW, claude_login=lambda: True)
    assert [a.name for a in s.agents()] == ["Claude", "GitHub"] and s.claude.level is Level.GREEN
    assert s.backlog["Ready"] == 5 and s.backlog_error is None
    d = s.diagnostic()
    for legacy in ("heartbeat", "kill switch", "PR atual", "executor", "DRY_RUN", "Codex", "Listener"):
        assert legacy not in d, legacy
    assert "ghp_" not in d and "token" not in d.lower()


def test_cli_login_state_is_read_from_the_cli_and_cached():
    calls = []
    now = [0.0]

    def run(cmd):
        calls.append(cmd)
        return 0, '{"loggedIn": true, "email": "x"}'
    col._LOGIN_CACHE.clear()
    assert col.claude_login_state(run=run, clock=lambda: now[0], which=lambda n: "C:/x/claude.cmd") is True
    assert col.claude_login_state(run=run, clock=lambda: now[0] + 10, which=lambda n: "C:/x/claude.cmd") is True
    assert len(calls) == 1                                                                 # cache: nao chama o CLI a cada ciclo
    now[0] = 400.0
    assert col.claude_login_state(run=lambda c: (0, '{"loggedIn": false}'), clock=lambda: now[0], which=lambda n: "x") is False
    assert col.claude_login_state(run=lambda c: (1, "boom"), clock=lambda: now[0] + 1000, which=lambda n: "x") is None      # falha = desconhecido, nunca "logado"
    assert col.claude_login_state(run=run, clock=lambda: 0, which=lambda n: None) is None                                      # sem CLI
    col._LOGIN_CACHE.clear()


