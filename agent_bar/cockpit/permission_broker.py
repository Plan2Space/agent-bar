"""permission_broker.py — responder pedidos de permissao de qualquer lugar do Studio, sem dialogo modal (UX F4b).

O pedido ACP fica esperando a resposta (`respond`) enquanto o Studio segue usavel. O broker guarda os pedidos em
aberto (id do Inbox -> pedido + respond) e qualquer superficie responde: o cartao no Chat, a aba Permissoes ou o painel
de Notificacoes. Regras:
- **uma resposta por pedido**: o primeiro a responder vence; respostas seguintes iguais nao fazem nada, diferentes
  levantam `AlreadyDecided` (mesma regra do Inbox, que grava a decisao antes de responder ao agente);
- **irreversivel so com humano** e a UI so chama `answer(..., allow)` depois do codigo de uso unico (IrreversibleDialog);
- **prazo**: pedido vencido (`expire`) responde ao agente como recusado e fica EXPIRED no Inbox, nunca pendurado;
- **encerramento**: `cancel_all` recusa tudo que estava aberto e registra o motivo.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from agent_bar.cockpit.acp import PermissionRequest
from agent_bar.cockpit.permission_inbox import AlreadyDecided, Entry, PermissionInbox

ALLOW_ONCE, DENY = "allow_once", "deny"


@dataclass
class _Open:
    req: PermissionRequest
    respond: Callable[[str | None], None]


class PermissionBroker:
    def __init__(self, inbox: PermissionInbox, on_change: Callable[[str], None] | None = None):
        self.inbox = inbox
        self.on_change = on_change
        self._open: dict[str, _Open] = {}
        self._lock = threading.RLock()

    def register(self, entry_id: str, req: PermissionRequest, respond: Callable[[str | None], None]) -> None:
        with self._lock:
            self._open[entry_id] = _Open(req, respond)
        self._changed(entry_id)

    def is_open(self, entry_id: str) -> bool:
        with self._lock:
            return entry_id in self._open

    def open_ids(self) -> list[str]:
        with self._lock:
            return list(self._open)

    def request(self, entry_id: str) -> PermissionRequest | None:
        with self._lock:
            o = self._open.get(entry_id)
        return o.req if o else None

    @staticmethod
    def option_for(req: PermissionRequest, choice: str) -> str | None:
        """`choice` = optionId exato, ou um tipo (allow_once, allow_always, reject_once) ou "deny" (= recusar)."""
        if choice == DENY:
            return next((str(o["optionId"]) for o in req.options if str(o.get("kind", "")).startswith("reject")), None)
        for o in req.options:
            if str(o.get("optionId")) == choice:
                return choice
        for o in req.options:
            if o.get("kind") == choice:
                return str(o.get("optionId"))
        raise ValueError(f"o agente nao ofereceu {choice!r}")

    def answer(self, entry_id: str, choice: str, by: str = "human") -> Entry:
        """Grava a decisao no Inbox e responde ao agente UMA vez. Idempotente para a mesma escolha."""
        with self._lock:
            o = self._open.get(entry_id)
            if o is None:                                                    # ja respondido em outra superficie
                entry = self.inbox.get(entry_id)
                if entry.status == "PENDING":
                    raise KeyError(f"{entry_id}: o agente nao espera mais esta resposta")
                same = (entry.status == "DENIED") if choice == DENY else (entry.decision == choice)
                if not same:
                    raise AlreadyDecided(f"{entry_id}: ja {entry.status} ({entry.decision})")
                return entry
            option = self.option_for(o.req, choice)                         # "deny" -> a opcao reject do agente, se houver
            kind = DENY if choice == DENY else next(
                (str(x.get("kind") or option) for x in o.req.options if str(x.get("optionId")) == option), str(option))
            entry = self.inbox.decide(entry_id, kind, by=by)                 # pode levantar (irreversivel sem humano)
            del self._open[entry_id]
        try:
            o.respond(option)
        finally:
            self._changed(entry_id)
        return entry

    def expire(self) -> list[str]:
        """Pedidos vencidos: EXPIRED no Inbox e recusa ao agente (o agente nao fica pendurado)."""
        self.inbox.expire_due()
        gone = []
        with self._lock:
            for eid, o in list(self._open.items()):
                if self.inbox.get(eid).status != "PENDING":
                    del self._open[eid]
                    gone.append((eid, o))
        for eid, o in gone:
            o.respond(None)
            self._changed(eid)
        return [eid for eid, _ in gone]

    def cancel_all(self, why: str) -> int:
        with self._lock:
            items = list(self._open.items())
            self._open.clear()
        for eid, o in items:
            self.inbox.cancel_one(eid, why)                                  # CANCELLED com o motivo (nao "negado")
            o.respond(None)
            self._changed(eid)
        return len(items)

    def _changed(self, entry_id: str) -> None:
        if self.on_change is not None:
            try:
                self.on_change(entry_id)
            except Exception:                                                 # noqa: BLE001 - UI nao derruba a resposta
                pass
