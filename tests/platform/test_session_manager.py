"""Session Manager minimo (F4): simultaneas, identidade, cancelamento e fechamento por sessao, limite por provedor,
persistencia e recuperacao (com e sem contexto), sem reenviar prompt sozinho."""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from agent_bar.platform.adapter import (
    AgentEvent,
    ProviderUnavailable,
    SessionNotFound,
    SessionRequest,
)
from agent_bar.platform.sessions import SessionBusy, SessionLimitReached, SessionManager
from tests.platform.fake_adapter import FakeAdapter
from tests.support.process import pid_alive


class Fake(FakeAdapter):
    """Provedor simulado com sessoes que sobrevivem ao processo (como o claude-agent-acp guarda em disco) quando ganha
    um `store` compartilhado, e com `crash()` para matar o processo do agente."""

    instances: list["Fake"] = []

    def __init__(self, store: dict | None = None, **kw):
        super().__init__(**kw)
        self.store = store
        self.closed: list[str] = []
        self.dead = False
        Fake.instances.append(self)

    def create_session(self, request):
        rec = super().create_session(request)
        new = f"fake-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._sessions[new] = {**self._sessions.pop(rec["session_id"]), "session_id": new}
            self._cancel[new] = self._cancel.pop(rec["session_id"])
        if self.store is not None:
            self.store[new] = dict(self._sessions[new])
        return dict(self._sessions[new])

    def resume_session(self, session_id):
        self._need()
        if session_id not in self._sessions and self.store is not None and session_id in self.store:
            with self._lock:
                self._sessions[session_id] = dict(self.store[session_id])
                self._cancel[session_id] = threading.Event()
        return super().resume_session(session_id)

    def close_session(self, session_id):
        super().close_session(session_id)
        self.closed.append(session_id)

    def crash(self):
        self.dead = True
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait(10)

    def _need(self):
        if self.dead:
            raise ProviderUnavailable("o processo do agente terminou")
        super()._need()


@pytest.fixture(autouse=True)
def _cleanup():
    Fake.instances.clear()
    yield
    for a in Fake.instances:
        a.dead = False
        a.disconnect()


def manager(tmp_path=None, store=None, **kw):
    return SessionManager({"a": lambda: Fake(store=store), "b": lambda: Fake(store=store)},
                          store_path=(tmp_path / "sessions.json") if tmp_path else None, **kw)


def test_sessions_on_two_providers_run_at_the_same_time_and_cancel_is_per_session():
    with manager() as m:
        s1, s2 = m.open("a", title="um"), m.open("b", SessionRequest(model_id="fake-think", effort="high"))
        assert s1["id"] != s2["id"] and s2["session"]["effort_accepted"] == "high"
        f1 = m.send(s1["id"], "conte devagar")
        f2 = m.send(s2["id"], "conte devagar")
        time.sleep(0.2)
        assert m.get(s1["id"])["state"] == "RUNNING" and m.get(s2["id"])["state"] == "RUNNING"
        with pytest.raises(SessionBusy):
            m.send(s1["id"], "outro")
        m.cancel(s1["id"])
        assert f1.result(5).stop_reason == "cancelled"
        assert not f2.done()                                                 # a outra continua
        m.cancel(s2["id"])
        assert f2.result(5).stop_reason == "cancelled"


def test_history_keeps_user_and_agent_turns():
    with manager() as m:
        s = m.open("a")
        assert m.send(s["id"], "diga ok").result(5).stop_reason == "end_turn"
        assert [(h["role"], h["text"]) for h in m.get(s["id"])["history"]] == [("user", "diga ok"), ("agent", "okok")]


def test_the_open_session_limit_refuses_with_a_reason_and_frees_on_close():
    with manager(limits={"a": 2}) as m:
        s1 = m.open("a")
        m.open("a")
        with pytest.raises(SessionLimitReached, match="2 sessoes abertas"):
            m.open("a")
        m.open("b")                                                          # o limite e por provedor
        m.close(s1["id"])
        m.open("a")


def test_close_closes_on_the_provider_and_the_last_close_ends_the_agent_process():
    m = manager()
    s1, s2 = m.open("a"), m.open("a")
    adapter = Fake.instances[0]
    pids = adapter.owned_pids()
    m.close(s1["id"])
    assert adapter.closed == [s1["session"]["session_id"]] and all(pid_alive(p) for p in pids)
    m.close(s2["id"])
    end = time.monotonic() + 5
    while time.monotonic() < end and any(pid_alive(p) for p in pids):
        time.sleep(0.05)
    assert not any(pid_alive(p) for p in pids) and m.get(s1["id"])["state"] == "CLOSED"


def test_a_crash_marks_every_session_of_that_provider_and_nothing_is_resent():
    with manager() as m:
        s1, s2, other = m.open("a"), m.open("a"), m.open("b")
        Fake.instances[0].crash()
        with pytest.raises(ProviderUnavailable):
            m.send(s1["id"], "diga ok")
        assert m.get(s1["id"])["state"] == "ERROR" and m.get(s2["id"])["state"] == "ERROR"
        assert m.get(other["id"])["state"] == "READY"
        assert [h["text"] for h in m.get(s1["id"])["history"]] == []          # nao entrou no historico nem foi reenviado
        with pytest.raises(ProviderUnavailable, match="recupere"):
            m.send(s1["id"], "diga ok")


def test_recovery_without_resume_opens_a_new_session_with_the_same_request_and_says_so():
    with manager() as m:
        s = m.open("a", SessionRequest(model_id="fake-think", effort="low"))
        old_psid = s["session"]["session_id"]
        Fake.instances[0].crash()
        with pytest.raises(ProviderUnavailable):
            m.send(s["id"], "diga ok")
        r = m.recover(s["id"])
        assert r["state"] == "READY" and r["recovered"] == 1 and r["recovered_without_context"] is True
        assert r["session"]["session_id"] != old_psid and r["id"] == s["id"]
        assert (r["session"]["model_accepted"], r["session"]["effort_accepted"]) == ("fake-think", "low")
        assert m.send(s["id"], "diga ok").result(5).stop_reason == "end_turn"


def test_recovery_with_resume_keeps_the_provider_session():
    store: dict = {}
    with manager(store=store) as m:
        s = m.open("a")
        Fake.instances[0].crash()
        with pytest.raises(ProviderUnavailable):
            m.send(s["id"], "diga ok")
        r = m.recover(s["id"])
        assert r["recovered_without_context"] is False and r["session"]["session_id"] == s["session"]["session_id"]


def test_sessions_persist_across_studio_restarts_and_come_back_closed(tmp_path):
    store: dict = {}
    m = manager(tmp_path, store=store)
    s = m.open("a", title="conversa")
    m.send(s["id"], "diga ok").result(5)
    for a in Fake.instances:                                                 # o Studio "morre" sem fechar
        a.disconnect()
    m2 = manager(tmp_path, store=store)
    back = m2.get(s["id"])
    assert back["state"] == "CLOSED" and back["title"] == "conversa" and len(back["history"]) == 2
    r = m2.recover(s["id"])
    assert r["state"] == "READY" and r["recovered_without_context"] is False
    m2.close_all()


def test_a_corrupt_store_starts_empty_instead_of_inventing(tmp_path):
    (tmp_path / "sessions.json").write_text("{nao e json", encoding="utf-8")
    assert manager(tmp_path).sessions() == []


def test_closing_twice_and_unknown_sessions_are_explicit():
    from agent_bar.platform.sessions import UnknownSession
    with manager() as m:
        s = m.open("a")
        m.close(s["id"])
        m.close(s["id"])                                                     # idempotente
        with pytest.raises(UnknownSession):
            m.send("p2s-nao-existe", "x")
        assert SessionNotFound                                               # import usado (erro do adaptador)


def test_events_reach_the_caller():
    with manager() as m:
        s = m.open("a")
        got: list[AgentEvent] = []
        m.send(s["id"], "diga ok", got.append).result(5)
        assert [e.kind for e in got] == ["status", "text", "text"]
