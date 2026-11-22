"""Agente ACP falso por stdio, para os testes de contrato do cockpit (sem rede, sem LLM).

O comportamento do `session/prompt` e escolhido pela primeira palavra do texto:
  echo N     -> N chunks "c0 ", "c1 ", ... e stopReason end_turn
  env        -> um chunk com o JSON ordenado das CHAVES do ambiente recebido
  caps       -> um chunk com o JSON das clientCapabilities recebidas no initialize
  perm       -> tool_call + session/request_permission; chunk "outcome=<selected:ID|cancelled>"; tool_call_update
  slow       -> chunks a cada 50 ms ate session/cancel -> stopReason cancelled
  garbage    -> uma linha invalida, depois um chunk "after-garbage"
  die        -> sai com codigo 3 no meio do prompt (sem responder)
  spawn      -> inicia um neto (python dormindo 120 s) e manda o PID dele num chunk
  tools      -> tool_call + 3 tool_call_update do mesmo toolCallId + plan + thought
  unknown    -> um session/update com sessionUpdate desconhecido, depois end_turn
  askfs      -> pede fs/read_text_file ao cliente e manda o erro recebido num chunk
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

_wlock = threading.Lock()
_waiting: dict[int, dict] = {}
_events: dict[int, threading.Event] = {}
_cancelled: set[str] = set()
_caps: dict = {}
_next = [1000]


def send(msg: dict) -> None:
    with _wlock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def raw(line: str) -> None:
    with _wlock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def update(sid: str, upd: dict) -> None:
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": upd}})


def chunk(sid: str, text: str, kind: str = "agent_message_chunk") -> None:
    update(sid, {"sessionUpdate": kind, "content": {"type": "text", "text": text}})


def ask(method: str, params: dict) -> dict:
    _next[0] += 1
    rid = _next[0]
    ev = threading.Event()
    _events[rid] = ev
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    ev.wait(30)
    return _waiting.pop(rid, {})


def handle_prompt(rid: int, params: dict) -> None:
    sid = params["sessionId"]
    text = " ".join(b.get("text", "") for b in params.get("prompt", []) if b.get("type") == "text").strip()
    word, _, rest = text.partition(" ")
    reason = "end_turn"
    if word == "echo":
        for i in range(int(rest or "3")):
            chunk(sid, f"c{i} ")
    elif word == "env":
        chunk(sid, json.dumps(sorted(os.environ)))
    elif word == "caps":
        chunk(sid, json.dumps(_caps, sort_keys=True))
    elif word == "perm":
        tc = {"toolCallId": "t1", "title": "Run rm", "kind": "execute", "status": "pending"}
        update(sid, {"sessionUpdate": "tool_call", **tc})
        res = ask("session/request_permission", {"sessionId": sid, "toolCall": tc, "options": [
            {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
            {"optionId": "always", "name": "Always", "kind": "allow_always"},
            {"optionId": "reject", "name": "Reject", "kind": "reject_once"}]})
        out = (res.get("result") or {}).get("outcome") or {}
        label = f"selected:{out.get('optionId')}" if out.get("outcome") == "selected" else str(out.get("outcome"))
        chunk(sid, f"outcome={label}")
        update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1",
                     "status": "completed" if label == "selected:allow" else "failed"})
    elif word == "slow":
        for i in range(400):
            if sid in _cancelled:
                reason = "cancelled"
                break
            chunk(sid, f"s{i} ")
            time.sleep(0.05)
    elif word == "garbage":
        raw("{this is not json")
        chunk(sid, "after-garbage")
    elif word == "die":
        chunk(sid, "dying")
        os._exit(3)
    elif word == "spawn":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        chunk(sid, str(child.pid))
    elif word == "tools":
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "x", "title": "Read a.py", "kind": "read",
                     "status": "pending"})
        for st in ("in_progress", "in_progress", "completed"):
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "x", "status": st})
        update(sid, {"sessionUpdate": "plan", "entries": [{"content": "ler", "priority": "high", "status": "completed"}]})
        chunk(sid, "hmm", kind="agent_thought_chunk")
    elif word == "unknown":
        update(sid, {"sessionUpdate": "something_new", "x": 1})
    elif word == "askfs":
        res = ask("fs/read_text_file", {"sessionId": sid, "path": "C:/x.txt"})
        chunk(sid, json.dumps(res.get("error", {}).get("code")))
    elif word == "acentos":
        chunk(sid, "ação é ü ✓")
    _cancelled.discard(sid)
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": reason}})


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")                # ACP e UTF-8 (o padrao do Windows e cp1252)
    sys.stdin.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:                                       # resposta a um pedido nosso
            _waiting[rid] = msg
            ev = _events.pop(rid, None)
            if ev:
                ev.set()
        elif method == "initialize":
            _caps.update(params.get("clientCapabilities") or {})
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1,
                                                          "agentCapabilities": {"loadSession": True}, "authMethods": []}})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "s-1", "modes": {
                "currentModeId": "default", "availableModes": [
                    {"id": "default", "name": "Ask"}, {"id": "acceptEdits", "name": "Accept edits"},
                    {"id": "plan", "name": "Plan"}, {"id": "bypassPermissions", "name": "Bypass"}]}}})
        elif method == "session/load":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/set_mode":
            update(params["sessionId"], {"sessionUpdate": "current_mode_update", "currentModeId": params["modeId"]})
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/prompt":
            threading.Thread(target=handle_prompt, args=(rid, params), daemon=True).start()
        elif method == "session/cancel":
            _cancelled.add(params.get("sessionId", ""))
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}})


if __name__ == "__main__":
    main()
