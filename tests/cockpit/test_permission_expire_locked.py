"""O vigia de prazos (ChatSessions._tick → broker.expire, a cada segundo) não pode levantar quando o SQLite está ocupado.

Achado em 2026-11-28: a suíte, com a máquina carregada, quebrava em teardown de test_run_vision com
`sqlite3.OperationalError: database is locked` dentro do slot do timer. A janela tem várias conexões no MESMO
permissions.sqlite (Inbox, NotificationCenter, ImageLedger); uma escrita longa de outra conexão faz o expire_due esperar
o busy_timeout e estourar. No app do Felipe seria a mesma exceção dentro de um slot Qt. Vencer prazo é manutenção
periódica: com o banco ocupado, pula esta volta (e diz por quê) e vence na próxima.
"""
from __future__ import annotations

import sqlite3

from agent_bar.cockpit.acp import PermissionRequest
from agent_bar.cockpit.permission_broker import PermissionBroker
from agent_bar.cockpit.permission_inbox import PermissionInbox

OPTIONS = ({"optionId": "allow", "name": "Allow", "kind": "allow_once"},
           {"optionId": "reject", "name": "Reject", "kind": "reject_once"})


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def test_a_busy_database_skips_this_expiry_round_and_the_next_round_expires(tmp_path):
    clock = Clock()
    db = tmp_path / "permissions.sqlite"
    inbox = PermissionInbox(db, clock=clock, ttl_s=60, busy_timeout=0.2)
    broker = PermissionBroker(inbox)
    tc = {"toolCallId": "t", "title": "Write a.py", "kind": "edit", "rawInput": {"file_path": "a.py"}}
    entry = inbox.record(tc, provider="p", session_id="s1")
    answers = []
    broker.register(entry.id, PermissionRequest("s1", tc, list(OPTIONS)), answers.append)
    clock.t += 61                                                             # o prazo venceu
    other = sqlite3.connect(db, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")                                          # outra conexão segura a escrita
    try:
        assert broker.expire() == []                                          # antes: OperationalError no slot
        assert inbox.get(entry.id).status == "PENDING" and answers == []      # nada vencido pela metade
        assert "ocupado" in broker.last_expire_skip
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert broker.expire() == [entry.id]                                      # próxima volta: vence e responde
    assert inbox.get(entry.id).status == "EXPIRED" and answers and broker.last_expire_skip == ""
    inbox.close()
