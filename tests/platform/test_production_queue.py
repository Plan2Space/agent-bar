"""Fila de producao local (P2S-LOCAL-FIRST-EXECUTION-001 §9). Cobra: job validado ao enfileirar; um por vez e na
ordem; maquina nao pronta = espera (SketchUp/V-Ray abertos, disco, usuario ativo, pytest); pausar/retomar; cancelar na
fila e com o job rodando (sem matar); worker morto -> interrupted -> retomar repete o mesmo job (replay real do
executor); um worker por fila; troca de versao do motor so explicita; camaras em jobs de ate 2; integracao com o
local_job deterministico e o motor FALSO."""
from __future__ import annotations

import json

import pytest

from agent_bar.platform import production_queue as pq
from tests.platform.test_local_job import SHA, Rig, job, server  # noqa: F401
from tests.platform.test_render_sample import CAMS, engine_root  # noqa: F401

READY = {"idle_s": 9999, "c_free_mb": 99999, "sketchup_open": False, "vray_open": False, "pytest_running": False}


class Runner:
    def __init__(self, status="SUCCEEDED", on_run=None):
        self.ran, self.status, self.on_run = [], status, on_run

    def __call__(self, j):
        self.ran.append(j["job_id"])
        if self.on_run:
            self.on_run(j)
        return {"status": self.status, "outcome_path": f"/out/{j['job_id']}/outcome.json"}


@pytest.fixture
def q(tmp_path):
    t = iter(range(10_000))
    return pq.Queue(tmp_path / "fila", clock=lambda: float(next(t)))


def test_jobs_run_one_at_a_time_in_order(q):
    for i in (1, 2, 3):
        q.enqueue(job(job_id=f"j-{i}"))
    run = Runner()
    assert q.work(run, lambda: READY, pq.Policy(), max_idle_polls=1, sleep=lambda s: None) == 3
    assert run.ran == ["j-1", "j-2", "j-3"]
    assert [j["state"] for j in q.list()] == ["succeeded"] * 3
    assert q.get("j-1")["machine"]["policy"] == {"idle_s": 600, "min_c_free_mb": 2048, "block_on_pytest": True}


@pytest.mark.parametrize("change, why", [
    ({"sketchup_open": True}, "SketchUp aberto"),
    ({"vray_open": True}, "V-Ray aberto"),
    ({"c_free_mb": 100}, "C: com 100 MB"),
    ({"idle_s": 30}, "usuario ativo"),
    ({"idle_s": None}, "usuario ativo"),                                     # sem como medir: nao comeca
    ({"pytest_running": True}, "pytest"),
])
def test_a_busy_machine_makes_the_job_wait(q, change, why):
    q.enqueue(job())
    run = Runner()
    r = q.work_once(run, lambda: {**READY, **change}, pq.Policy())
    assert run.ran == [] and any(why in w for w in r["waiting"]) and q.get("j-1")["state"] == "queued"


def test_the_policy_can_relax_idle_and_pytest_explicitly(q):
    q.enqueue(job())
    run = Runner()
    q.work_once(run, lambda: {**READY, "idle_s": 0, "pytest_running": True},
                pq.Policy(idle_s=0, block_on_pytest=False))
    assert run.ran == ["j-1"]


def test_pause_and_resume(q):
    q.enqueue(job())
    q.pause()
    run = Runner()
    assert q.work_once(run, lambda: READY, pq.Policy()) == {"waiting": ["fila pausada"]} and run.ran == []
    q.resume()
    assert q.work_once(run, lambda: READY, pq.Policy())["ran"] == "j-1"


def test_cancel_a_queued_job(q):
    q.enqueue(job())
    assert q.cancel("j-1")["state"] == "cancelled"
    run = Runner()
    q.work_once(run, lambda: READY, pq.Policy())
    assert run.ran == []


def test_a_job_cancelled_while_the_machine_is_checked_never_starts(q):
    q.enqueue(job())
    run = Runner()
    r = q.work_once(run, lambda: (q.cancel("j-1"), READY)[1], pq.Policy())  # cancelado entre a leitura e o inicio
    assert run.ran == [] and q.get("j-1")["state"] == "cancelled" and "mudou de estado" in r["waiting"][0]


def test_cancel_while_running_never_kills_and_keeps_the_real_outcome(q):
    q.enqueue(job())
    run = Runner(on_run=lambda j: q.cancel(j["job_id"]))
    q.work_once(run, lambda: READY, pq.Policy())
    j = q.get("j-1")
    assert j["cancel_requested"] == 1 and j["state"] == "succeeded" and "termina" in j["note"]


def test_failed_and_refused_outcomes_map_to_states(q):
    q.enqueue(job(job_id="a"))
    q.enqueue(job(job_id="b"))
    q.work_once(Runner("TOOL_ERROR"), lambda: READY, pq.Policy())
    q.work_once(Runner("REFUSED"), lambda: READY, pq.Policy())
    assert (q.get("a")["state"], q.get("b")["state"]) == ("failed", "refused")


def test_a_crashing_runner_is_recorded_as_infra(q):
    q.enqueue(job())

    def boom(j):
        raise OSError("disco")
    r = q.work_once(boom, lambda: READY, pq.Policy())
    assert r["status"] == "FAILED" and q.get("j-1")["state"] == "failed"


def test_a_dead_worker_leaves_interrupted_and_retry_replays_the_same_job(q, server, engine_root, tmp_path):
    rig = Rig(server, engine_root, tmp_path)
    q.enqueue(job())
    q.db.execute("UPDATE jobs SET state='running' WHERE job_id='j-1'")      # o worker morreu aqui
    first = rig.run(job())                                                   # ...mas o motor terminou o build
    q2 = pq.Queue(q.root)
    q2.recover()
    assert q2.get("j-1")["state"] == "interrupted"
    q2.retry_interrupted("j-1")
    out = {}

    def run(j):
        out.update(rig.run(j))
        return out
    q2.work_once(run, lambda: READY, pq.Policy())
    assert out["request_id"] == first["request_id"] and len(rig.engine.calls) == 1   # replay: nada construido de novo
    assert q2.get("j-1")["state"] == "succeeded"


def test_only_one_worker_per_queue(q):
    (q.root / "worker.lock").write_text(json.dumps({"pid": 4, "created": 0.0}), encoding="utf-8")
    import agent_bar.platform.proc as proc
    orig = proc.same_process
    proc.same_process = lambda pid, created: True                            # "outro worker vivo"
    try:
        with pytest.raises(RuntimeError, match="worker vivo"):
            q.work(Runner(), lambda: READY, pq.Policy(), once=True)
    finally:
        proc.same_process = orig


def test_a_stale_lock_from_a_dead_worker_is_taken_over(q):
    (q.root / "worker.lock").write_text(json.dumps({"pid": 4, "created": 0.0}), encoding="utf-8")
    q.enqueue(job())
    assert q.work(Runner(), lambda: READY, pq.Policy(), once=True) == 1
    assert not (q.root / "worker.lock").exists()


def test_an_invalid_or_duplicate_job_is_never_enqueued(q):
    with pytest.raises(ValueError, match="JOB_INVALID"):
        q.enqueue(job(task_type="delete"))
    q.enqueue(job())
    with pytest.raises(ValueError, match="ja esta na fila"):
        q.enqueue(job())


def test_reprocessing_with_a_new_engine_is_explicit_and_linked(q):
    q.enqueue(job())
    new = "b" * 40
    [r] = q.reprocess(["j-1"], new)
    assert r["job_id"] == "j-1.ebbbbbbbb" and r["job"]["engine_ref"]["sha40"] == new and r["parent_job_id"] == "j-1"
    assert q.get("j-1")["job"]["engine_ref"]["sha40"] == SHA                 # o original nao muda


def test_all_cameras_become_jobs_of_at_most_two():
    jobs = pq.camera_jobs("lx-1", ["a", "b", "c"], SHA, prefix="cams", executor={"kind": "deterministic"})
    assert [j["inputs"]["cameras"] for j in jobs] == [["a", "b"], ["c"]]
    assert all(pq.validate_job(j) == [] for j in jobs)


def test_end_to_end_with_the_deterministic_local_job(q, server, engine_root, tmp_path):
    rig = Rig(server, engine_root, tmp_path)
    q.enqueue(job(job_id="b", inputs={"fixture_ref": "plan_alt_5x3_e3a"}))
    q.work(lambda j: rig.run(j), lambda: READY, pq.Policy(), once=True)
    b = rig.run(job(job_id="b", inputs={"fixture_ref": "plan_alt_5x3_e3a"}))     # replay: o mesmo build
    for j in pq.camera_jobs(b["run_id"], CAMS, SHA, prefix="cams", executor={"kind": "deterministic"}):
        q.enqueue(j)
    q.work(lambda j: rig.run(j), lambda: READY, pq.Policy(), max_idle_polls=1, sleep=lambda s: None)
    assert [j["state"] for j in q.list()] == ["succeeded", "succeeded"]
    assert len(rig.engine.calls) == 2 and rig.chats() == []                 # 1 build + 1 render, nenhum modelo


# ------------------------------------------------------------------------------- revisao #224
def test_the_worker_recovers_a_job_left_running_before_it_takes_the_queue(q):
    q.enqueue(job())
    q.db.execute("UPDATE jobs SET state='running' WHERE job_id='j-1'")      # worker anterior morreu aqui
    run = Runner()
    q.work(run, lambda: READY, pq.Policy(), max_idle_polls=1, sleep=lambda s: None)
    assert q.get("j-1")["state"] == "interrupted" and run.ran == []         # nao roda sozinho: retomar e explicito


def _outcome(j, status):
    return {"schema": "p2s.local_outcome.v1", "job_id": j["job_id"], "task_type": j["task_type"], "status": status,
            "error": None if status == "SUCCEEDED" else "INFRA:SKETCHUP_BUSY", "executor_id": "deterministic",
            "tokens_measured": 0, "tokens_estimated": None, "cloud": {"tokens": 0, "providers_called": []},
            "assistance": [], "permissions": [], "failure": None, "started_at": "t0", "finished_at": "t1"}


def test_a_resumed_job_that_fails_is_what_the_router_reads(q, tmp_path):
    results = iter(["SUCCEEDED", "FAILED"])
    run = pq.recording_runner(tmp_path / "out", lambda j: _outcome(j, next(results)))
    q.enqueue(job())
    q.work_once(run, lambda: READY, pq.Policy())
    q.db.execute("UPDATE jobs SET state='interrupted' WHERE job_id='j-1'")  # retomar: o MESMO job_id roda de novo
    q.retry_interrupted("j-1")
    r = q.work_once(run, lambda: READY, pq.Policy())
    d = tmp_path / "out" / "j-1"
    assert r["status"] == "FAILED" and q.get("j-1")["state"] == "failed"
    assert json.loads((d / "outcome.json").read_text(encoding="utf-8"))["status"] == "FAILED"
    assert json.loads((d / "outcome.1.json").read_text(encoding="utf-8"))["status"] == "SUCCEEDED"
    assert q.get("j-1")["outcome_path"] == str(d / "outcome.json")


def test_a_crashing_executor_still_replaces_the_contract_outcome(q, tmp_path):
    def boom(j):
        raise OSError("ollama caiu")
    run = pq.recording_runner(tmp_path / "out", boom)
    q.enqueue(job())
    r = q.work_once(run, lambda: READY, pq.Policy())
    cur = json.loads((tmp_path / "out" / "j-1" / "outcome.json").read_text(encoding="utf-8"))
    assert r["status"] == "FAILED" and cur["error"] == "INFRA:RUNNER_ERROR: OSError"
