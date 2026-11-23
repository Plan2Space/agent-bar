"""Mede a carga de sessoes num adaptador ACP real (F5-B), sem prompt e sem custo de tokens.

Uso: python -m agent_bar.platform.tools.measure_session_load [N] <saida.json>
Abre sessoes no MESMO processo do adaptador ate N (padrao 40), mede processos e memoria da arvore e a latencia
de abertura, para no primeiro erro e fecha tudo. Resultado usado para fixar DEFAULT_LIMITS em sessions.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

from agent_bar.platform.acp_adapter import AcpAdapter
from agent_bar.platform.adapter import SessionRequest

STEPS = {1, 2, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 48}


def tree(root: int) -> tuple[int, int]:
    """(processos, bytes de working set) da arvore de `root`."""
    ps = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,WorkingSetSize | ConvertTo-Json -Compress"
    rows = json.loads(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout)
    kids: dict[int, list[dict]] = {}
    for r in rows:
        kids.setdefault(r["ParentProcessId"], []).append(r)
    by_id = {r["ProcessId"]: r for r in rows}
    total, count = (int(by_id[root]["WorkingSetSize"] or 0), 1) if root in by_id else (0, 0)
    stack = [root]
    while stack:
        for k in kids.get(stack.pop(), []):
            total += int(k["WorkingSetSize"] or 0)
            count += 1
            stack.append(k["ProcessId"])
    return count, total


def measure(n: int, argv: list[str] | None = None) -> dict:
    a = AcpAdapter(argv or ["claude-agent-acp"], tempfile.mkdtemp(prefix="f5-load-"), env=dict(os.environ))
    rows: list[dict] = []
    error = None
    t0 = time.monotonic()
    a.connect()
    root = a.owned_pids()[0]
    c, m = tree(root)
    rows.append({"sessions": 0, "processes": c, "mem_mb": round(m / 2**20), "open_s": None})
    try:
        for i in range(1, n + 1):
            t = time.monotonic()
            try:
                a.create_session(SessionRequest(model_id="sonnet", effort="low"))
            except Exception as exc:  # noqa: BLE001 - o erro e o resultado
                error = {"at": i, "error": f"{type(exc).__name__}: {exc}"[:300]}
                break
            if i in STEPS:
                c, m = tree(root)
                rows.append({"sessions": i, "processes": c, "mem_mb": round(m / 2**20), "open_s": round(time.monotonic() - t, 2)})
                print(json.dumps(rows[-1]), flush=True)
    finally:
        a.disconnect()
    time.sleep(3)
    return {"agent": " ".join(argv or ["claude-agent-acp"]), "max_tried": n, "rows": rows, "error": error,
            "after_disconnect_processes": tree(root)[0], "elapsed_s": round(time.monotonic() - t0, 1)}


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 2 else 40
    out = sys.argv[-1]
    result = measure(n)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print(json.dumps({"error": result["error"], "after_disconnect_processes": result["after_disconnect_processes"]}))
    return 0 if result["error"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
