"""demo_agent.py — agente ACP de DEMONSTRACAO (sem LLM, sem rede) para exercitar o cockpit antes do agente real.

Responde a qualquer prompt com um roteiro fixo: plano, uma leitura de arquivo, um pedido de permissao para um comando
e um texto final que deixa claro que nao ha modelo por tras. Nao le nem escreve nada no disco.
"""
from __future__ import annotations

import json
import sys
import threading
import time

_lock = threading.Lock()
_answers: dict[int, dict] = {}
_events: dict[int, threading.Event] = {}
_cancel: set[str] = set()


def _send(msg: dict) -> None:
    with _lock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _upd(sid: str, **u) -> None:
    _send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})


def _say(sid: str, text: str, kind: str = "agent_message_chunk", delay: float = 0.02) -> None:
    for word in text.split(" "):
        if sid in _cancel:
            return
        _upd(sid, sessionUpdate=kind, content={"type": "text", "text": word + " "})
        time.sleep(delay)


def _turn(rid: int, sid: str, text: str) -> None:
    _say(sid, "Vou entender o pedido e montar um plano curto.", kind="agent_thought_chunk", delay=0.01)
    plan = [{"content": "Ler o contexto do workspace", "priority": "high", "status": "in_progress"},
            {"content": "Pedir permissão para rodar os testes", "priority": "medium", "status": "pending"},
            {"content": "Responder", "priority": "low", "status": "pending"}]
    _upd(sid, sessionUpdate="plan", entries=plan)
    _upd(sid, sessionUpdate="tool_call", toolCallId="demo-read", title="Ler README.md", kind="read", status="in_progress")
    time.sleep(0.2)
    _upd(sid, sessionUpdate="tool_call_update", toolCallId="demo-read", status="completed",
         content=[{"type": "content", "content": {"type": "text", "text": "(demonstração: nenhum arquivo foi lido)"}}])
    plan[0]["status"], plan[1]["status"] = "completed", "in_progress"
    _upd(sid, sessionUpdate="plan", entries=plan)
    tc = {"toolCallId": "demo-test", "title": "pytest -q", "kind": "execute", "status": "pending"}
    _upd(sid, sessionUpdate="tool_call", **tc)
    _events[rid * 1000] = ev = threading.Event()
    _send({"jsonrpc": "2.0", "id": rid * 1000, "method": "session/request_permission", "params": {
        "sessionId": sid, "toolCall": tc, "options": [
            {"optionId": "allow", "name": "Permitir uma vez", "kind": "allow_once"},
            {"optionId": "reject", "name": "Recusar", "kind": "reject_once"}]}})
    ev.wait(600)
    out = ((_answers.pop(rid * 1000, {}).get("result") or {}).get("outcome") or {})
    allowed = out.get("outcome") == "selected" and out.get("optionId") == "allow"
    _upd(sid, sessionUpdate="tool_call_update", toolCallId="demo-test", status="completed" if allowed else "failed",
         content=[{"type": "content", "content": {"type": "text", "text": "(demonstração: nenhum comando foi executado)"}}])
    plan[1]["status"], plan[2]["status"] = "completed", "completed"
    _upd(sid, sessionUpdate="plan", entries=plan)
    verdict = "permitiu" if allowed else "recusou"
    _say(sid, f"Isto é o agente de DEMONSTRAÇÃO do cockpit: não há modelo nem execução real. Você {verdict} o comando. "
              f"Pedido recebido: \"{text[:200]}\".")
    reason = "cancelled" if sid in _cancel else "end_turn"
    _cancel.discard(sid)
    _send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": reason}})


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")                # ACP e UTF-8 (o padrao do Windows e cp1252)
    sys.stdin.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, rid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:
            _answers[rid] = msg
            ev = _events.pop(rid, None)
            if ev:
                ev.set()
        elif method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": False},
                                                          "authMethods": []}})
        elif method == "session/new":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "demo-1", "modes": {"currentModeId": "default",
                   "availableModes": [{"id": "default", "name": "Perguntar"}, {"id": "plan", "name": "Planejar"}]}}})
        elif method == "session/set_mode":
            _upd(p.get("sessionId", ""), sessionUpdate="current_mode_update", currentModeId=p.get("modeId"))
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/prompt":
            text = " ".join(b.get("text", "") for b in p.get("prompt", []) if isinstance(b, dict))
            threading.Thread(target=_turn, args=(rid, p.get("sessionId", ""), text), daemon=True).start()
        elif method == "session/cancel":
            sid = p.get("sessionId", "")
            _cancel.add(sid)
            for ev in list(_events.values()):
                ev.set()
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "não suportado"}})


if __name__ == "__main__":
    main()
