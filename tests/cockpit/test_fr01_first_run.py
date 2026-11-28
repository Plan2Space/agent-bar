"""FR-01 (FRIENDLY-AUDIT): primeiro uso com lista de verificação.

Não havia primeiro uso: a instalação pede claude, o adaptador claude-agent-acp, gh com login, Ollama e uma pasta de
projeto, e nada disso era checado nem explicado na tela. Agora a página "Vamos preparar o P2S Studio" abre na 1ª
execução (sem `first_run_done` no settings.json) e quando falta algo essencial; cada item diz Pronto/Falta/Opcional, uma frase
simples e um botão. Itens opcionais ausentes não bloqueiam "Começar". Acessível depois em Configurações → Primeiro uso.
"""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar import settings as S  # noqa: E402
from agent_bar.cockpit import first_run as FR  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FORBIDDEN = ("ACP", "MCP", "acp_mcp.json", "bypass", "workspace", "Default", "recommended", "PASS", "FAIL", "CANDIDATE",
             "evidence", "design-library", "E:\\", "rc=", "WinError", "pid", "EventStore", "EVENT_STORE_DB_PATH",
             "AGENT_BAR_", "allow_once", "slot", "digest")


def env(tmp_path, *, have=("claude", "claude-agent-acp", "gh", "ollama"), claude_login=True, gh_login=True,
        folders=True, engine=True) -> FR.Env:
    folder = tmp_path / "projeto"
    folder.mkdir(exist_ok=True)
    return FR.Env(which=lambda name: f"C:/bin/{name}.exe" if name in have else None,
                  claude_login=lambda: claude_login, gh_login=lambda: gh_login,
                  folders=lambda: [folder] if folders else [], engine=lambda: (engine, "" if engine else "sem motor"))


def by_key(checks):
    return {c.key: c for c in checks}


def test_a_missing_adapter_is_a_red_item_whose_button_copies_the_exact_command(tmp_path):
    checks = by_key(FR.collect(env(tmp_path, have=("claude", "gh", "ollama"))))
    acp = checks["adapter"]
    assert acp.state == FR.MISSING and acp.essential
    assert acp.action == ("copy", "npm install -g @agentclientprotocol/claude-agent-acp")


def test_the_page_opens_on_a_fresh_profile_and_the_copy_button_copies(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))     # perfil vazio
    e = env(tmp_path, have=("claude", "gh", "ollama"))
    assert FR.should_open(FR.collect(e), S.load().first_run_done) is True
    from agent_bar.cockpit.ui.first_run import FirstRunPage
    page = FirstRunPage(env_fn=lambda: e)
    try:
        row = page.rows["adapter"]
        assert row.glyph.text() == "Falta"
        row.button.click()
        assert QtWidgets.QApplication.clipboard().text() == "npm install -g @agentclientprotocol/claude-agent-acp"
        assert not page.start_btn.isEnabled()                                 # falta algo essencial
    finally:
        page.deleteLater()


def test_with_everything_present_and_done_the_page_does_not_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    FR.mark_done()
    assert S.load().first_run_done is True
    assert FR.should_open(FR.collect(env(tmp_path)), S.load().first_run_done) is False


def test_done_but_an_essential_item_missing_opens_again(tmp_path):
    assert FR.should_open(FR.collect(env(tmp_path, folders=False)), True) is True


def test_optional_items_missing_do_not_block_start(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    e = env(tmp_path, have=("claude", "claude-agent-acp", "gh"), engine=False)   # sem Ollama e sem motor
    checks = by_key(FR.collect(e))
    assert checks["ollama"].state == FR.OPTIONAL and checks["engine"].state == FR.OPTIONAL
    assert FR.should_open(list(checks.values()), True) is False
    from agent_bar.cockpit.ui.first_run import FirstRunPage
    page = FirstRunPage(env_fn=lambda: e)
    done = []
    page.done.connect(lambda: done.append(True))
    try:
        assert page.rows["ollama"].glyph.text() == "Opcional"
        assert page.start_btn.isEnabled()
        page.start_btn.click()
        assert done == [True] and S.load().first_run_done is True
    finally:
        page.deleteLater()


def test_recheck_reads_the_machine_again(tmp_path):
    from agent_bar.cockpit.ui.first_run import FirstRunPage
    state = {"have": ("claude", "gh")}
    page = FirstRunPage(env_fn=lambda: env(tmp_path, have=state["have"]))
    try:
        assert page.rows["adapter"].glyph.text() == "Falta"
        state["have"] = ("claude", "gh", "claude-agent-acp")
        page.recheck_btn.click()
        assert page.rows["adapter"].glyph.text() == "Pronto"
    finally:
        page.deleteLater()


def test_choose_folder_item_uses_the_chooser_and_remembers(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    folder = tmp_path / "escolhida"
    folder.mkdir()
    from agent_bar.cockpit import project_folders as PF
    from agent_bar.cockpit.store import data_dir
    from agent_bar.cockpit.ui.first_run import FirstRunPage
    page = FirstRunPage(env_fn=lambda: env(tmp_path, folders=False), choose_folder=lambda parent: str(folder))
    try:
        assert page.rows["folder"].button.text() == "Escolher pasta…"
        page.rows["folder"].button.click()
        assert PF.load(data_dir() / "preferences.json") == [folder.resolve()]
    finally:
        page.deleteLater()


def test_the_page_text_has_no_forbidden_terms(tmp_path):
    from agent_bar.cockpit.ui.first_run import FirstRunPage
    for e in (env(tmp_path), env(tmp_path, have=(), claude_login=False, gh_login=False, folders=False, engine=False)):
        page = FirstRunPage(env_fn=lambda e=e: e)
        try:
            texts = [w.text() for w in [*page.findChildren(QtWidgets.QLabel), *page.findChildren(QtWidgets.QAbstractButton)]]
            text = "\n".join(texts)
            assert "Vamos preparar o P2S Studio" in text
            for term in FORBIDDEN:
                assert not re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text), (term, text)
        finally:
            page.deleteLater()


def test_the_settings_has_a_first_run_section(tmp_path):
    from agent_bar.cockpit.ui import settings_tab
    assert "Primeiro uso" in settings_tab.SECTIONS


@pytest.mark.parametrize("needed", [True, False])
def test_opening_the_studio_goes_to_first_run_only_when_needed(needed):
    from types import SimpleNamespace

    from agent_bar import core as C
    calls = []

    class Win:
        _closed = False
        chat = SimpleNamespace(ensure_client=lambda: calls.append("chat"))

        def show(self): pass                                                     # noqa: E704

        def raise_(self): pass                                                   # noqa: E704

        def activateWindow(self): pass                                           # noqa: E704,N802

        def open_first_run(self):
            calls.append("first_run")
    core = C.AppCore(S.Settings(), quit_fn=lambda: None, tray_available=lambda: False, cockpit_factory=Win,
                     first_run_check=lambda: needed)
    core.open_cockpit()
    assert calls == (["first_run"] if needed else ["chat"])
    core.open_cockpit()                                                            # reabrir a mesma janela não repete
    assert calls[-1] == "chat"


def test_the_first_run_section_in_settings_builds_the_page(tmp_path):
    from agent_bar.cockpit.ui.settings_tab import SettingsTab
    tab = SettingsTab(first_run_kwargs={"env_fn": lambda: env(tmp_path)})
    try:
        assert tab.first_run_page is None                                           # nada checado antes da visita
        tab.show_section("Primeiro uso")
        assert tab.first_run_page is not None and "adapter" in tab.first_run_page.rows
    finally:
        tab.deleteLater()
