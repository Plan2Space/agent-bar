"""UX-05 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9): cada dado diz a fonte, o commit lido, a hora da leitura e se está ao
vivo, em cache ou indisponível. Erro com dado anterior nunca aparece sem "desatualizado desde"."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import data_state as DS

T0 = 1795400000.0                                                                 # 23/11/2026 02:13:20 UTC
FMT = lambda t: time.strftime("%H:%M:%S", time.gmtime(t))                         # noqa: E731 - determinístico


def git_repo(path: Path, branch: str = "develop") -> str:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True)  # noqa: E731
    run("init", "-q", "-b", branch)
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    return run("rev-parse", "--short=7", "HEAD").stdout.strip()


def test_a_live_read_names_source_branch_commit_and_time():
    st = DS.live("evidence", r"E:\Claude\apps\evidence", T0, DS.Checkout("develop", "d3455fd"))
    assert st.line(FMT) == r"evidence E:\Claude\apps\evidence · develop d3455fd · lido às 02:13:20 · ao vivo"


def test_an_error_after_a_good_read_is_cache_and_always_says_outdated_since():
    good = DS.live("evidence", "E:/ev", T0, DS.Checkout("develop", "d3455fd"))
    st = DS.failed("evidence", "E:/ev", "verify.py falhou", good)
    assert st.status == DS.CACHE and st.read_at == T0
    assert st.line(FMT) == "evidence E:/ev · develop d3455fd · desatualizado desde 02:13:20 (a leitura falhou: verify.py falhou)"
    again = DS.failed("evidence", "E:/ev", "de novo", st)                         # falhou de novo: continua desde T0
    assert again.read_at == T0 and "desatualizado desde 02:13:20" in again.line(FMT)


def test_an_error_without_a_previous_read_is_unavailable_and_shows_no_data_or_time():
    st = DS.failed("motor", "E:/motor", "pasta não existe", None)
    assert st.status == DS.INDISPONIVEL and st.line(FMT) == "motor E:/motor · indisponível: pasta não existe"


@pytest.mark.parametrize("prev", [None, DS.live("x", "p", T0), DS.failed("x", "p", "e", DS.live("x", "p", T0))])
def test_no_state_after_an_error_ever_renders_as_current(prev):
    line = DS.failed("x", "p", "erro", prev).line(FMT)
    assert "ao vivo" not in line and ("desatualizado desde" in line or "indisponível" in line)


def test_checkout_reads_branch_commit_and_local_changes_of_a_real_git_folder(tmp_path):
    sha = git_repo(tmp_path / "ev")
    co = DS.checkout(tmp_path / "ev")
    assert (co.branch, co.commit, co.dirty) == ("develop", sha, False) and co.text() == f"develop {sha}"
    (tmp_path / "ev" / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path / "ev"), "add", "f.txt"], check=True)
    assert DS.checkout(tmp_path / "ev").dirty is True
    assert DS.checkout(tmp_path / "nao-git-" ).error == "pasta não existe"
    (tmp_path / "plain").mkdir()
    assert DS.checkout(tmp_path / "plain").text().startswith("sem commit")


# ------------------------------------------------------------------- Revisão visual e barra de estado (offscreen)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait(cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def studio(tmp_path, monkeypatch):
    import sys

    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit import evidence as E
    from agent_bar.cockpit import gallery as G
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    from tests.cockpit.gallery_fixture import PLANT, ROOM, build_all
    ev, engine, dl = build_all(tmp_path)
    shas = {"evidence": git_repo(ev, "develop"), "motor": git_repo(engine, "main")}
    monkeypatch.setenv("AGENT_BAR_ENGINE_DIR", str(engine))
    monkeypatch.setenv("AGENT_BAR_DESIGN_LIBRARY_DIR", str(dl))                  # design-library sem git
    stub = Path(__file__).with_name("fake_acp_agent.py")
    state = {"fail": False}

    def load(root):
        if state["fail"]:
            raise E.EvidenceError("verify.py do checkout falhou")
        return E.load_catalog(root, expected={})
    win = CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                        workspaces_fn=lambda: [tmp_path / "x"],
                        review_kwargs={"root_fn": lambda: ev, "load_fn": load,
                                       "gallery_fn": lambda cat: G.build_gallery(cat, engine, dl, rooms=[(PLANT, ROOM)])})
    try:
        yield win, ev, engine, dl, shas, state
    finally:
        win.shutdown()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_gallery_and_the_status_bar_show_each_checkout_commit_and_read_time(studio):
    win, ev, engine, dl, shas, _ = studio
    tab = win.review
    tab.clock = lambda: T0
    tab.reload()
    assert wait(lambda: {"evidence", "motor", "design-library"} <= set(tab.data_states)
                and all(n in tab._checkouts for n in ("evidence", "motor", "design-library")))  # noqa: SLF001
    text = tab.sources_label.text()
    assert f"evidence {ev} · develop {shas['evidence']} · lido às" in text and "ao vivo" in text
    assert f"motor {engine} · main {shas['motor']}" in text
    assert f"design-library {dl} · sem commit (não é checkout git)" in text
    assert win.status_sources.text() == text                                     # a mesma linha na barra de estado
    assert win.status_sources.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Policy.Ignored


def test_a_failed_reload_after_a_good_one_says_outdated_since_or_unavailable(studio):
    win, ev, _engine, _dl, shas, state = studio
    tab = win.review
    tab.reload()
    assert wait(lambda: "evidence" in tab.data_states and tab.data_states["evidence"].status == DS.AO_VIVO)
    state["fail"] = True
    tab.reload()
    assert wait(lambda: tab.data_states["evidence"].status == DS.CACHE)
    line = tab.data_states["evidence"].line()
    assert "desatualizado desde" in line and "verify.py do checkout falhou" in line
    assert f"develop {shas['evidence']}" in line                                  # o commit do que está na tela


def test_the_cached_line_keeps_the_commit_that_was_read_when_the_new_path_fails(studio):
    win, ev, _engine, _dl, shas, _state = studio
    tab = win.review
    tab.reload()
    assert wait(lambda: "evidence" in tab._checkouts and tab.data_states.get("evidence")                # noqa: SLF001
                and tab.data_states["evidence"].checkout is not None)
    tab.root_fn = lambda: ev.parent / "nao-existe"                                 # o caminho novo some
    tab.reload()
    assert wait(lambda: tab.data_states["evidence"].status != DS.AO_VIVO)
    wait(lambda: False, 1.0)                                                       # o probe do caminho novo chega
    line = tab.data_states["evidence"].line()
    assert f"develop {shas['evidence']}" in line and "pasta não existe" not in line.split("(a leitura falhou")[0]
    assert "evidence" in tab.sources_label.text() and "desatualizado desde" in win.status_sources.text()
    assert tab.sources_label.styleSheet() != tab_ok_css()


def tab_ok_css():
    from agent_bar.cockpit.ui.review_tab import SOURCES_OK_CSS
    return SOURCES_OK_CSS


def test_a_live_state_never_takes_the_commit_probed_for_another_path(studio):
    win, ev, _engine, _dl, _shas, _state = studio
    tab = win.review
    tab._checkout_read(tab.req, "evidence", (str(ev), DS.Checkout("develop", "aaaaaaa")))   # noqa: SLF001
    tab._set_state("evidence", ev.parent / "outro-checkout")                                 # noqa: SLF001
    assert tab.data_states["evidence"].checkout is None                          # o commit era de outro caminho
    tab._set_state("evidence", ev)                                                            # noqa: SLF001
    assert tab.data_states["evidence"].checkout == DS.Checkout("develop", "aaaaaaa")
