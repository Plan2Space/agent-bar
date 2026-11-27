"""production_queue.py — fila de producao local (pedido do Felipe P2S-LOCAL-FIRST-EXECUTION-001 §9).

Enfileira jobs `p2s.local_job.v1` (build/render; deterministico ou por modelo local) e os executa UM POR VEZ pelo
`local_job.run_job`, com o motor de PRODUCAO fixado por sha (nunca uma worktree de desenvolvimento).

Regras:
- um SketchUp por vez: um unico worker por fila (lock por PID) e o lock do executor; antes de cada job a maquina e
  conferida: nenhum SketchUp/V-Ray aberto, disco C: com folga, usuario ocioso e nenhuma suite pytest rodando (cada
  condicao configuravel e registrada no job). Maquina nao pronta = o job ESPERA, nada e forcado;
- pausar a fila = o worker nao comeca o proximo job (o que esta rodando termina); retomar = volta a pegar;
- cancelar job na fila = `cancelled` na hora. Job RODANDO nao e morto (matar o Python deixaria o SketchUp orfao): fica
  `cancel_requested` e o desfecho real do motor e registrado;
- worker que morreu com job `running` -> o job vira `interrupted`; retomar roda o MESMO job+tentativa, e o executor
  devolve o resultado real do motor por replay (ou RESULT_UNKNOWN), sem construir de novo;
- trocar a versao do motor e explicito: `reprocess` cria jobs NOVOS com o sha novo declarado, ligados ao original;
- o desfecho de cada job e o `outcome.json` do local_job (nunca sobrescrito).

    python -m agent_bar.platform.production_queue --queue <dir> enqueue <job.json> [...]
    python -m agent_bar.platform.production_queue --queue <dir> list | pause | resume | cancel <job_id>
    python -m agent_bar.platform.production_queue --queue <dir> cameras <run_id> --sha40 <sha> [--model M]
    python -m agent_bar.platform.production_queue --queue <dir> reprocess <job_id> [...] --sha40 <novo>
    python -m agent_bar.platform.production_queue --queue <dir> work [--once] [--idle-s 600]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_bar.platform.local_job import JOB_SCHEMA, validate_job

STATES = ("queued", "running", "succeeded", "failed", "refused", "cancelled", "interrupted")
FINAL = ("succeeded", "failed", "refused", "cancelled")
OUTCOME_STATE = {"SUCCEEDED": "succeeded", "REFUSED": "refused", "CANCELLED": "cancelled"}   # o resto: failed


@dataclass(frozen=True)
class Policy:
    """Condicoes para comecar um job. Todas entram no registro do job."""
    idle_s: int = 600                                                         # 0 = nao exige usuario ocioso
    min_c_free_mb: int = 2048
    block_on_pytest: bool = True


# ------------------------------------------------------------------------------------------------ maquina
def _idle_s() -> int | None:
    if os.name != "nt":
        return None

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LII(ctypes.sizeof(LII), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) // 1000


def _procs() -> list[str]:
    if os.name != "nt":
        return []
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, timeout=20,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return [line.split(b'","')[0].strip(b'"').decode("ascii", "replace").lower() for line in out.splitlines() if line]


def _pytest_running() -> bool:
    if os.name != "nt":
        return False
    q = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'pytest' -and "
         "$_.CommandLine -notmatch 'Get-CimInstance' } | Measure-Object | Select-Object -ExpandProperty Count")
    n = subprocess.run(["powershell", "-NoProfile", "-Command", q], capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    return bool(n) and n != "0"


def machine_state() -> dict:
    """Estado lido da maquina (so leitura)."""
    procs = _procs()
    c_free = shutil.disk_usage("C:\\").free // 2**20 if os.name == "nt" else None
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "idle_s": _idle_s(), "c_free_mb": c_free,
            "sketchup_open": any(p.startswith("sketchup") for p in procs),
            "vray_open": any(p.startswith("vray") for p in procs), "pytest_running": _pytest_running()}


def blockers(state: dict, policy: Policy) -> list[str]:
    """Motivos para NAO comecar um job agora (lista vazia = pode)."""
    why = []
    if state.get("sketchup_open"):
        why.append("SketchUp aberto (um por vez)")
    if state.get("vray_open"):
        why.append("V-Ray aberto")
    c = state.get("c_free_mb")
    if c is not None and c < policy.min_c_free_mb:
        why.append(f"C: com {c} MB livres (< {policy.min_c_free_mb})")
    idle = state.get("idle_s")
    if policy.idle_s and (idle is None or idle < policy.idle_s):
        why.append(f"usuario ativo (ocioso {idle}s < {policy.idle_s}s)")
    if policy.block_on_pytest and state.get("pytest_running"):
        why.append("suite pytest rodando")
    return why


# ------------------------------------------------------------------------------------------------ fila
class Queue:
    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(str(self.root / "queue.sqlite"), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, seq INTEGER NOT NULL, state TEXT NOT NULL,
                job TEXT NOT NULL, enqueued_at REAL NOT NULL, started_at REAL, finished_at REAL, outcome_path TEXT,
                outcome_status TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, machine TEXT, note TEXT,
                parent_job_id TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)

    # -------------------------------------------------------------------------------------------- estado
    def _meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def _set_meta(self, key: str, value: str | None) -> None:
        self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    @property
    def paused(self) -> bool:
        return self._meta("paused", "0") == "1"

    def pause(self) -> None:
        self._set_meta("paused", "1")

    def resume(self) -> None:
        self._set_meta("paused", "0")

    def get(self, job_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["job"] = json.loads(d["job"])
        d["machine"] = json.loads(d["machine"]) if d["machine"] else None
        return d

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY seq")]

    # -------------------------------------------------------------------------------------------- comandos
    def enqueue(self, job: dict, parent_job_id: str | None = None) -> dict:
        errs = validate_job(job)
        if errs:
            raise ValueError("JOB_INVALID: " + "; ".join(errs))
        if self.get(job["job_id"]) is not None:
            raise ValueError(f"job_id {job['job_id']} ja esta na fila (um job = um registro; tentativa nova = id novo)")
        seq = (self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM jobs").fetchone()[0] or 0) + 1
        self.db.execute("INSERT INTO jobs(job_id, seq, state, job, enqueued_at, parent_job_id) VALUES(?,?,?,?,?,?)",
                        (job["job_id"], seq, "queued", json.dumps(job, sort_keys=True), self.clock(), parent_job_id))
        return self.get(job["job_id"])

    def cancel(self, job_id: str) -> dict:
        j = self.get(job_id)
        if j is None:
            raise KeyError(job_id)
        if j["state"] in ("queued", "interrupted"):
            self.db.execute("UPDATE jobs SET state='cancelled', finished_at=?, note=? WHERE job_id=?",
                            (self.clock(), "cancelado antes de rodar", job_id))
        elif j["state"] == "running":                                         # nao mata: o motor fecha o proprio SketchUp
            self.db.execute("UPDATE jobs SET cancel_requested=1, note=? WHERE job_id=?",
                            ("cancelamento pedido com o job rodando: ele termina e o desfecho real fica registrado",
                             job_id))
        return self.get(job_id)

    def reprocess(self, job_ids: list[str], sha40: str) -> list[dict]:
        """Troca de versao EXPLICITA: jobs novos com o sha novo declarado, ligados ao original."""
        out = []
        for jid in job_ids:
            j = self.get(jid)
            if j is None:
                raise KeyError(jid)
            new = {**j["job"], "job_id": f"{jid}.e{sha40[:8]}", "attempt": 1, "feedback": None,
                   "engine_ref": {**j["job"]["engine_ref"], "sha40": sha40}}
            out.append(self.enqueue(new, parent_job_id=jid))
        return out

    def recover(self) -> list[str]:
        """Jobs `running` sem worker vivo (o worker morreu) viram `interrupted`."""
        ids = [r["job_id"] for r in self.db.execute("SELECT job_id FROM jobs WHERE state='running'")]
        for jid in ids:
            self.db.execute("UPDATE jobs SET state='interrupted', note=? WHERE job_id=?",
                            ("o worker parou com o job rodando; retomar repete o mesmo job+tentativa (replay)", jid))
        return ids

    def retry_interrupted(self, job_id: str) -> dict:
        j = self.get(job_id)
        if j is None or j["state"] != "interrupted":
            raise ValueError(f"{job_id} nao esta interrupted")
        self.db.execute("UPDATE jobs SET state='queued', note=? WHERE job_id=?",
                        ("retomado: mesmo job+tentativa, o executor reconcilia pelo request_id", job_id))
        return self.get(job_id)

    def next_queued(self) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY seq LIMIT 1").fetchone()
        return self._row(row) if row else None

    # -------------------------------------------------------------------------------------------- worker
    def _lock(self) -> bool:
        """Um worker por fila: lock com o PID e o instante de criacao (o Windows reaproveita PID)."""
        from agent_bar.platform.proc import creation_time, same_process
        path = self.root / "worker.lock"
        me = {"pid": os.getpid(), "created": creation_time(os.getpid())}
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                other = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                other = {}
            if other.get("pid") == me["pid"]:
                return True
            if other.get("pid") and same_process(int(other["pid"]), other.get("created")):
                return False                                                  # outro worker vivo
            path.unlink(missing_ok=True)                                      # lock velho: worker morto
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(me, fh)
        return True

    def _unlock(self) -> None:
        (self.root / "worker.lock").unlink(missing_ok=True)

    def work_once(self, run: Callable[[dict], dict], state: Callable[[], dict], policy: Policy) -> dict:
        """Uma passada: devolve {"ran": job_id, ...} ou {"waiting": motivo}. Nunca roda dois jobs."""
        if self.paused:
            return {"waiting": ["fila pausada"]}
        job = self.next_queued()
        if job is None:
            return {"waiting": ["fila vazia"]}
        m = state()
        why = blockers(m, policy)
        if why:
            return {"waiting": why, "job_id": job["job_id"], "machine": m}
        jid = job["job_id"]
        cur = self.db.execute("UPDATE jobs SET state='running', started_at=?, machine=? WHERE job_id=? AND "
                              "state='queued'", (self.clock(), json.dumps({**m, "policy": policy.__dict__}), jid))
        if cur.rowcount != 1:                                                 # cancelado no meio: nao roda
            return {"waiting": [f"{jid} mudou de estado antes de comecar"]}
        try:
            outcome = run(job["job"])
        except Exception as exc:                                              # noqa: BLE001 - vira registro
            outcome = {"status": "FAILED", "error": f"INFRA:WORKER {type(exc).__name__}: {str(exc)[:200]}",
                       "outcome_path": None}
        state_now = OUTCOME_STATE.get(str(outcome.get("status")), "failed")
        self.db.execute("UPDATE jobs SET state=?, finished_at=?, outcome_status=?, outcome_path=? WHERE job_id=?",
                        (state_now, self.clock(), outcome.get("status"), outcome.get("outcome_path"), jid))
        return {"ran": jid, "status": outcome.get("status"), "state": state_now}

    def work(self, run: Callable[[dict], dict], state: Callable[[], dict], policy: Policy, *, once: bool = False,
             sleep: Callable[[float], None] = time.sleep, poll_s: float = 30.0,
             log: Callable[[dict], None] = lambda r: None, max_idle_polls: int | None = None) -> int:
        if not self._lock():
            raise RuntimeError("ja ha um worker vivo nesta fila (um SketchUp por vez)")
        try:
            self.recover()
            ran, idle = 0, 0
            while True:
                r = self.work_once(run, state, policy)
                log(r)
                if "ran" in r:
                    ran, idle = ran + 1, 0
                    if once:
                        return ran
                    continue
                if once and r["waiting"] == ["fila vazia"]:
                    return ran
                idle += 1
                if max_idle_polls is not None and idle >= max_idle_polls:
                    return ran
                sleep(poll_s)
        finally:
            self._unlock()


# ------------------------------------------------------------------------------------------------ geradores
def camera_jobs(run_id: str, cameras: list[str], sha40: str, *, prefix: str, executor: dict) -> list[dict]:
    """Renderizar TODAS as camaras de um build: jobs de no maximo 2 camaras (limite do motor)."""
    out = []
    for i in range(0, len(cameras), 2):
        out.append({"schema": JOB_SCHEMA, "job_id": f"{prefix}-{i // 2 + 1}", "task_type": "render",
                    "executor": dict(executor), "inputs": {"run_id": run_id, "cameras": cameras[i:i + 2]},
                    "engine_ref": {"repo": "Plan2Space/sketchup-mcp-next", "sha40": sha40}, "attempt": 1,
                    "feedback": None})
    return out


def default_runner(out: Path, endpoint: str) -> Callable[[dict], dict]:
    """Roda um job pelo local_job no mesmo processo e grava o outcome (como a CLI do local_job)."""
    from agent_bar.platform.local_job import run_job
    from agent_bar.platform.render_sample import RenderSampleTool, ToolBox
    from agent_bar.platform.sketchup_sample import executor_adapter, git_head, sample_tool_from_env

    def run(job: dict) -> dict:
        tool = sample_tool_from_env()
        outcome = run_job(job, build_host=lambda: ToolBox([tool, RenderSampleTool(tool)]),
                          local_adapter=lambda: executor_adapter(endpoint), engine_root=tool.engine_root,
                          production=os.environ.get("P2S_PRODUCTION_ENGINE"), head=git_head)
        target = out / job["job_id"]
        target.mkdir(parents=True, exist_ok=True)
        path = target / "outcome.json"
        if path.exists():
            path = target / f"outcome.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        path.write_text(json.dumps(outcome, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        if outcome.get("failure"):
            with open(out / "failures.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(outcome["failure"], ensure_ascii=False, default=str) + "\n")
        return {**outcome, "outcome_path": str(path)}
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fila de producao local (um SketchUp por vez)")
    ap.add_argument("--queue", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enqueue")
    e.add_argument("jobs", nargs="+")
    sub.add_parser("list")
    sub.add_parser("pause")
    sub.add_parser("resume")
    c = sub.add_parser("cancel")
    c.add_argument("job_id")
    r = sub.add_parser("retry")
    r.add_argument("job_id")
    cam = sub.add_parser("cameras")
    cam.add_argument("run_id")
    cam.add_argument("--camera", action="append", required=True)
    cam.add_argument("--sha40", required=True)
    cam.add_argument("--model")
    rp = sub.add_parser("reprocess")
    rp.add_argument("job_ids", nargs="+")
    rp.add_argument("--sha40", required=True)
    w = sub.add_parser("work")
    w.add_argument("--once", action="store_true")
    w.add_argument("--idle-s", type=int, default=600)
    w.add_argument("--endpoint", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    args = ap.parse_args(argv)
    q = Queue(Path(args.queue))
    show: Any
    if args.cmd == "enqueue":
        show = [q.enqueue(json.loads(Path(p).read_text(encoding="utf-8")))["job_id"] for p in args.jobs]
    elif args.cmd == "list":
        show = {"paused": q.paused, "jobs": [{k: j[k] for k in ("job_id", "state", "outcome_status", "note",
                                                                 "parent_job_id")} for j in q.list()]}
    elif args.cmd in ("pause", "resume"):
        getattr(q, args.cmd)()
        show = {"paused": q.paused}
    elif args.cmd == "cancel":
        show = q.cancel(args.job_id)["state"]
    elif args.cmd == "retry":
        show = q.retry_interrupted(args.job_id)["state"]
    elif args.cmd == "cameras":
        ex = {"kind": "local", "model_id": args.model, "adapter": "ollama"} if args.model else {"kind": "deterministic"}
        show = [q.enqueue(j)["job_id"] for j in camera_jobs(args.run_id, args.camera, args.sha40,
                                                             prefix=f"cams-{args.run_id}", executor=ex)]
    elif args.cmd == "reprocess":
        show = [j["job_id"] for j in q.reprocess(args.job_ids, args.sha40)]
    else:
        def log(rec: dict) -> None:
            with open(q.root / "worker.log", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec},
                                    ensure_ascii=False, default=str) + "\n")
        show = {"ran": q.work(default_runner(q.root / "outcomes", args.endpoint), machine_state,
                              Policy(idle_s=args.idle_s), once=args.once, log=log)}
    print(json.dumps(show, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
