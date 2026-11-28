"""local_job.py — "executar a tarefa X com o executor Y" para o roteador Local First (agent-runtime, Frente E).

Contrato combinado com a Frente E (2026-11-27), fronteira por arquivo/CLI (o agent-runtime nao importa o agent-bar):

    python -m agent_bar.platform.local_job run <job.json> --out <dir>   ->  <dir>/<job_id>/outcome.json

- entrada `p2s.local_job.v1`, saida `p2s.local_outcome.v1` (schemas em docs/contracts/);
- `executor.kind = deterministic`: o motor roda DIRETO, sem LLM (fixture conhecida -> build; build -> render).
  Zero token. E o caminho padrao de operacao conhecida (pedido do Felipe: nao usar IA para o que e deterministico);
- `executor.kind = local`: um modelo do Ollama recebe o pedido em linguagem natural e opera as MESMAS ferramentas do
  Studio; o `feedback` do roteador entra na tentativa seguinte e fica registrado em `assistance` (nada escondido);
- pino do motor: recusa `REFUSED`/`ENGINE_NOT_PINNED` se o motor configurado nao e o checkout de producao
  (`P2S_PRODUCTION_ENGINE`), se o HEAD difere de `engine_ref.sha40` ou se o checkout esta sujo. Trocar de versao e um
  job novo com outro sha, nunca automatico;
- falha do motor/maquina/provedor = `FAILED` com error `INFRA:<code>` (nao conta contra o modelo);
- toda falha atribuivel ao executor gera um registro `p2s.local_failure.v1` (o roteador e as skills consomem);
- nenhum provedor cloud no caminho: `cloud.tokens = 0` e `cloud.providers_called = []`, por construcao (modelo
  `…cloud` do Ollama e endpoint fora do loopback sao recusados);
- `<dir>/<job_id>/outcome.json` e sempre o desfecho da execucao mais recente (ver `record_outcome`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent_bar.platform.compat_bench import INFERENCE, MODEL_CODES, RecordingHost, run_turn
from agent_bar.platform.local_tools import ToolCall, ToolContext, call_digest

JOB_SCHEMA = "p2s.local_job.v1"
OUTCOME_SCHEMA = "p2s.local_outcome.v1"
FAILURE_SCHEMA = "p2s.local_failure.v1"
ENGINE_REPO = "Plan2Space/sketchup-mcp-next"
TASK_TYPES = ("build", "render")
KINDS = ("local", "deterministic")
STATUSES = ("SUCCEEDED", "FAILED", "TIMEOUT", "TOOL_ERROR", "REFUSED", "CANCELLED")
FAILURE_CLASSES = ("PROTOCOL", "SCHEMA", "CONTEXT", "TOOL", "CAPABILITY")
_JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^lx-[0-9A-Za-z_-]+$")
_CLOUD_MODEL = re.compile(r"(?:^|[:-])cloud$")                             # gpt-oss:120b-cloud, qwen3-coder:cloud
PROTOCOL_CODES = frozenset({"UNKNOWN_TOOL", "MALFORMED", "DUPLICATE", "TURN_LIMIT"})


# ------------------------------------------------------------------------------------------------ job
def validate_job(job: Any) -> list[str]:
    """Erros do job (lista vazia = valido). Nada roda com job invalido."""
    if not isinstance(job, dict):
        return ["job nao e um objeto JSON"]
    errs = []
    if job.get("schema") != JOB_SCHEMA:
        errs.append(f"schema deve ser {JOB_SCHEMA}")
    if not isinstance(job.get("job_id"), str) or not _JOB_ID.match(job["job_id"]):
        errs.append("job_id: 1-80 caracteres [A-Za-z0-9._-]")
    tt = job.get("task_type")
    if tt not in TASK_TYPES:
        errs.append(f"task_type deve ser um de {TASK_TYPES}")
    ex = job.get("executor")
    if not isinstance(ex, dict) or ex.get("kind") not in KINDS:
        errs.append(f"executor.kind deve ser um de {KINDS}")
    elif ex["kind"] == "local":
        if not isinstance(ex.get("model_id"), str) or not ex["model_id"]:
            errs.append("executor.model_id obrigatorio para kind=local")
        if ex.get("adapter", "ollama") != "ollama":
            errs.append("executor.adapter: so 'ollama' (provedor local)")
        if isinstance(ex.get("model_id"), str) and _CLOUD_MODEL.search(ex["model_id"]):
            errs.append("executor.model_id: modelo cloud do Ollama roda fora da maquina (so modelo local)")
    inp = job.get("inputs")
    if not isinstance(inp, dict):
        errs.append("inputs deve ser um objeto")
    elif tt == "build":
        if set(inp) != {"fixture_ref"} or not isinstance(inp.get("fixture_ref"), str):
            errs.append("inputs de build: so {fixture_ref: string}")
    elif tt == "render":
        cams = inp.get("cameras")
        if set(inp) != {"run_id", "cameras"} or not isinstance(inp.get("run_id"), str) \
                or not _RUN_ID.match(inp["run_id"]) or not isinstance(cams, list) or not 1 <= len(cams) <= 2 \
                or not all(isinstance(c, str) for c in cams) or len(set(cams)) != len(cams):
            errs.append("inputs de render: {run_id: lx-..., cameras: 1 ou 2 nomes distintos}")
    ref = job.get("engine_ref")
    if not isinstance(ref, dict) or ref.get("repo") != ENGINE_REPO or not isinstance(ref.get("sha40"), str) \
            or not _SHA40.match(ref["sha40"]):
        errs.append(f"engine_ref: {{repo: {ENGINE_REPO}, sha40: 40 hex minusculos}}")
    att = job.get("attempt", 1)
    if not isinstance(att, int) or isinstance(att, bool) or att < 1:
        errs.append("attempt: inteiro >= 1")
    fb = job.get("feedback")
    if fb is not None and (not isinstance(fb, dict) or fb.get("failure_class") not in FAILURE_CLASSES):
        errs.append(f"feedback: null ou {{failure_class em {FAILURE_CLASSES}, attempt, executor_id, error}}")
    return errs


def engine_pin_refusal(engine_root: Path, production: str | None, sha40: str,
                       head: Callable[[Path], dict | None]) -> str | None:
    """Motivo para recusar ENGINE_NOT_PINNED, ou None se o motor e o de producao, no sha pedido e limpo."""
    if not production:
        return "P2S_PRODUCTION_ENGINE nao definido: sem checkout de producao declarado"
    if Path(engine_root).resolve() != Path(production).resolve():
        return f"o motor configurado ({engine_root}) nao e o checkout de producao ({production})"
    h = head(Path(engine_root))
    if not h or not h.get("commit"):
        return "o checkout de producao nao e um checkout git legivel"
    if h["commit"] != sha40:
        return f"HEAD do motor {h['commit']} != engine_ref.sha40 {sha40} (trocar de versao e um job explicito)"
    if h.get("dirty") is not False:
        return "o checkout de producao tem mudancas locais (ou nao foi possivel conferir)"
    return None


def endpoint_refusal(endpoint: str) -> str | None:
    """Motivo para recusar NOT_LOCAL, ou None se o Ollama e o desta maquina (loopback). OLLAMA_HOST remoto executaria
    o modelo fora daqui sem nenhum registro de nuvem."""
    host = urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}").hostname or ""
    if host == "localhost" or host == "::1" or host.startswith("127."):
        return None
    return f"o endpoint do Ollama ({endpoint}) nao e loopback: o modelo rodaria fora desta maquina"


def executor_id(job: dict, digest: str | None = None) -> str:
    ex = job["executor"]
    if ex["kind"] == "deterministic":
        return "deterministic"
    return f"ollama:{ex['model_id']}@{(digest or 'sem-digest')[:8]}"


def tool_for(job: dict) -> tuple[str, dict]:
    """(ferramenta, argumentos) que cumprem o job: e o mesmo par que o deterministico usa e o exemplo do feedback."""
    from agent_bar.platform.render_sample import RENDER_TOOL
    from agent_bar.platform.sketchup_sample import TOOL_NAME
    if job["task_type"] == "build":
        return TOOL_NAME, {"fixture_ref": job["inputs"]["fixture_ref"]}
    return RENDER_TOOL, {"run_id": job["inputs"]["run_id"], "cameras": list(job["inputs"]["cameras"])}


def prompt_for(job: dict) -> str:
    """Pedido em linguagem natural para kind=local (fixo por tipo; o modelo escolhe e chama a ferramenta)."""
    inp = job["inputs"]
    if job["task_type"] == "build":
        text = f"Construa no SketchUp a amostra {inp['fixture_ref']}."
    else:
        text = f"Renderize com V-Ray o build {inp['run_id']} nas cameras {' e '.join(inp['cameras'])}."
    fb = job.get("feedback")
    if fb:
        text += (f"\nA tentativa anterior falhou ({fb.get('failure_class')}): {str(fb.get('error') or '')[:300]}. "
                 "Corrija a chamada da ferramenta.")
    return text


# ------------------------------------------------------------------------------------------------ desfecho
def classify(job: dict, calls: list[dict], refusals: list[dict], turn_error: str | None) -> dict:
    """Desfecho do executor a partir do que chegou ao Studio e do resultado do motor. Nunca da frase do modelo."""
    want_tool, want_args = tool_for(job)
    if turn_error:
        return {"status": "FAILED", "error": f"INFRA:PROVIDER {turn_error}", "failure_class": "TOOL", "infra": True}
    if refusals:
        r = refusals[0]
        return {"status": "TOOL_ERROR", "error": f"{r['code']}: {r.get('detail', '')}"[:400],
                "failure_class": "PROTOCOL", "infra": False, "tool": want_tool}
    if not calls:
        return {"status": "FAILED", "error": "o modelo nao chamou nenhuma ferramenta", "failure_class": "CAPABILITY",
                "infra": False, "tool": want_tool}
    c = calls[-1]
    res = c.get("result") or {}
    code = (res.get("error") or {}).get("code")
    if code in PROTOCOL_CODES:
        return {"status": "TOOL_ERROR", "error": f"{code}", "failure_class": "PROTOCOL", "infra": False,
                "tool": c["name"]}
    if code in MODEL_CODES:
        return {"status": "TOOL_ERROR", "error": f"{code}: {(res.get('error') or {}).get('message', '')}"[:400],
                "failure_class": "SCHEMA", "infra": False, "tool": c["name"]}
    if c["name"] != want_tool or dict(c["arguments"]) != want_args:
        return {"status": "TOOL_ERROR", "error": f"pediu {c['name']} {c['arguments']}, o job era {want_tool} {want_args}",
                "failure_class": "CONTEXT", "infra": False, "tool": c["name"]}
    st = res.get("status")
    if st == "completed":
        return {"status": "SUCCEEDED", "error": None, "failure_class": None, "infra": False, "tool": c["name"]}
    if st == "unknown" or code == "ENGINE_TIMEOUT":
        return {"status": "TIMEOUT", "error": f"INFRA:{code or 'RESULT_UNKNOWN'}", "failure_class": "TOOL",
                "infra": True, "tool": c["name"]}
    if code == "CANCELLED":
        return {"status": "CANCELLED", "error": "cancelado", "failure_class": None, "infra": True, "tool": c["name"]}
    return {"status": "FAILED", "error": f"INFRA:{code or st}", "failure_class": "TOOL", "infra": True,
            "tool": c["name"]}


def failure_record(job: dict, ex_id: str, verdict: dict, calls: list[dict], refusals: list[dict], source: str) -> dict:
    """Registro reutilizavel de uma falha (pedido do Felipe, adendo §6). Campos sem base ficam null, nunca inventados."""
    want_tool, want_args = tool_for(job)
    sent = (calls[-1]["arguments"] if calls else (refusals[0].get("raw") if refusals else None))
    invalid = None
    adapter_fix = None
    if calls and verdict["failure_class"] in ("SCHEMA", "CONTEXT"):
        got = dict(calls[-1]["arguments"])
        for name in sorted(set(want_args) | set(got)):
            if got.get(name) != want_args.get(name):
                invalid = {"name": name, "sent": got.get(name), "expected": want_args.get(name)}
                break
        if invalid and isinstance(invalid["sent"], str) and isinstance(invalid["expected"], list):
            adapter_fix = "array enviado como string: coercao estrita de JSON (agent-bar#148)"
    return {"schema": FAILURE_SCHEMA, "executor_id": ex_id, "task_type": job["task_type"], "tool": verdict.get("tool"),
            "failure_class": verdict["failure_class"], "error": verdict["error"], "invalid_argument": invalid,
            "contract_misread": verdict["error"] if verdict["failure_class"] in ("SCHEMA", "PROTOCOL") else None,
            "sequence": [c["name"] for c in calls] or None, "sent": sent,
            "helpful_example": {"tool": want_tool, "arguments": want_args},
            "adapter_fix": adapter_fix, "skill_hint": None, "source": source}


# ------------------------------------------------------------------------------------------------ execucao
def _auto_allow(log: list[dict], by: str) -> Callable:
    def on_permission(req, respond) -> None:
        tc = dict(getattr(req, "tool_call", {}) or {})
        log.append({"title": tc.get("title"), "decided": "allow_once", "decided_by": by})
        respond("allow_once")
    return on_permission


def run_deterministic(job: dict, host: Any, perms: list[dict]) -> tuple[list[dict], list[dict], None, None]:
    """Chama a ferramenta do Studio DIRETO, sem modelo: provider `runtime`, zero token."""
    name, args = tool_for(job)
    rec = RecordingHost(host)
    call = ToolCall(f"job-{job['job_id']}", name, args, call_digest(name, args), {"function": {"name": name,
                                                                                               "arguments": args}})
    attempt = int(job.get("attempt", 1))                                     # mesmo job+tentativa = mesmo pedido (replay)
    ctx = ToolContext(session_id=f"job-{job['job_id']}", turn=attempt, turn_id=f"{job['job_id']}#{attempt}",
                      model_id="deterministic", model_digest=None, provider_version=None, workspace=None,
                      on_permission=_auto_allow(perms, "local_job (deterministico, autorizado pelo job)"),
                      cancel=threading.Event(), emit=lambda k, d: None, provider="runtime", adapter="local_job")
    try:
        rec.run(call, ctx)
    except Exception:                                                         # noqa: BLE001 - ja registrada como EXECUTOR_ERROR
        pass                                                                  # (RecordingHost): vira INFRA no desfecho
    return rec.calls, [], None, None


def run_local(job: dict, adapter: Any, perms: list[dict], timeout_s: float,
              clock: Callable[[], float]) -> tuple[list[dict], list[dict], str | None, dict]:
    """Um turno do modelo pelo MESMO adapter e ferramentas do Studio. Devolve (chamadas, recusas, erro, turno)."""
    from agent_bar.platform.adapter import SessionRequest
    from agent_bar.platform.compat_bench import _refusals
    host = adapter.tool_host if isinstance(adapter.tool_host, RecordingHost) else RecordingHost(adapter.tool_host)
    adapter.tool_host = host
    adapter.on_permission = _auto_allow(perms, "local_job (autorizado pelo job)")
    n = len(host.calls)
    sid = adapter.create_session(SessionRequest(model_id=job["executor"]["model_id"]))["session_id"]
    try:
        turn = run_turn(adapter, sid, prompt_for(job), timeout_s, clock)
    finally:
        adapter.close_session(sid)
    return host.calls[n:], _refusals([turn]), turn["error"], turn


def _tokens(turn: dict | None) -> int | None:
    if not turn:
        return None
    total, seen = 0, False
    for e in turn["events"]:
        if e["kind"] != "usage":
            continue
        for k in ("input_tokens", "output_tokens"):
            v = (e["data"].get(k) or {}).get("value")
            if isinstance(v, (int, float)):
                total += int(v)
                seen = True
    return total if seen else None


def run_job(job: dict, *, build_host: Callable[[], Any], local_adapter: Callable[[], Any], engine_root: Path,
            production: str | None, head: Callable[[Path], dict | None], timeout_s: float = 3600.0,
            clock: Callable[[], float] = time.monotonic,
            wall: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            endpoint: str | None = None) -> dict:
    started = wall()
    errs = validate_job(job)
    base = {"schema": OUTCOME_SCHEMA, "job_id": job.get("job_id") if isinstance(job, dict) else None,
            "task_type": job.get("task_type") if isinstance(job, dict) else None, "started_at": started,
            "cloud": {"tokens": 0, "providers_called": []}, "assistance": [], "permissions": []}
    if errs:
        return {**base, "status": "REFUSED", "error": "JOB_INVALID: " + "; ".join(errs), "executor_id": None,
                "tokens_measured": None, "tokens_estimated": None, "failure": None, "finished_at": wall()}
    why = engine_pin_refusal(engine_root, production, job["engine_ref"]["sha40"], head)
    eng = head(Path(engine_root)) or {}
    base.update(engine={"repo": ENGINE_REPO, "root": str(engine_root), "sha40": eng.get("commit"),
                        "dirty": eng.get("dirty"), "pinned": job["engine_ref"]["sha40"]}, attempt=job.get("attempt", 1))
    if why:
        return {**base, "status": "REFUSED", "error": f"ENGINE_NOT_PINNED: {why}", "executor_id": executor_id(job),
                "tokens_measured": None, "tokens_estimated": None, "failure": None, "finished_at": wall()}
    remote = endpoint_refusal(endpoint) if endpoint and job["executor"]["kind"] == "local" else None
    if remote:
        return {**base, "status": "REFUSED", "error": f"NOT_LOCAL: {remote}", "executor_id": executor_id(job),
                "tokens_measured": None, "tokens_estimated": None, "failure": None, "finished_at": wall()}
    perms: list[dict] = base["permissions"]
    turn = None
    digest = None
    if job["executor"]["kind"] == "deterministic":
        calls, refusals, terr, _ = run_deterministic(job, build_host(), perms)
        tokens = 0
    else:
        adapter = local_adapter()
        adapter.inference_options = dict(INFERENCE)
        adapter.connect()
        try:
            try:
                digest = adapter.model_digest(job["executor"]["model_id"])
            except Exception:                                                 # noqa: BLE001 - modelo ausente: o turno diz
                digest = None
            calls, refusals, terr, turn = run_local(job, adapter, perms, timeout_s, clock)
        finally:
            adapter.disconnect()
        tokens = _tokens(turn)
        if job.get("feedback"):
            base["assistance"].append({"kind": "structured_feedback", "from": "roteador (agent-runtime)",
                                       "content": job["feedback"], "attempt": job.get("attempt", 1)})
    ex_id = executor_id(job, digest)
    verdict = classify(job, calls, refusals, terr)
    res = (calls[-1]["result"] if calls else {}) or {}
    artifacts = []
    if res.get("skp_abs"):
        artifacts.append({"kind": "skp", "path": res["skp_abs"], "sha256": res.get("skp_sha256")})
    for img in res.get("images") or []:
        artifacts.append({"kind": "render", "path": img.get("png_abs"), "sha256": img.get("sha256")})
    failure = None
    if verdict["status"] != "SUCCEEDED" and not verdict["infra"]:
        failure = failure_record(job, ex_id, verdict, calls, refusals, source=f"local_job:{job['job_id']}")
    out = {**base, "status": verdict["status"], "error": verdict["error"], "executor_id": ex_id,
           "failure_class": verdict["failure_class"], "tokens_measured": tokens, "tokens_estimated": None,
           "run_id": res.get("run_id"), "request_id": res.get("request_id"), "artifacts": artifacts,
           "tool_calls": [{"name": c["name"], "arguments": c["arguments"], "status": (c["result"] or {}).get("status"),
                           "error": (c["result"] or {}).get("error"),
                           "argument_coercion": (c["result"] or {}).get("argument_coercion")} for c in calls],
           "refusals": refusals, "failure": failure, "finished_at": wall()}
    if turn is not None:
        out["turn"] = {k: turn[k] for k in ("prompt", "stop_reason", "model_confirmed", "latency_s", "model_text")}
    return out


def record_outcome(out: Path, dir_name: str, job: Any, execute: Callable[[], dict],
                   wall: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   ) -> tuple[dict, Path]:
    """Executa e grava o desfecho no caminho do contrato, `<out>/<dir_name>/outcome.json`, que o roteador le.

    Esse arquivo e SEMPRE o da execucao mais recente do job_id (retomar/repetir o mesmo job_id e caminho previsto):
    antes de executar, o anterior sai do caminho para o historico `outcome.<n>.json` (o roteador nunca le um desfecho
    velho enquanto a nova execucao roda); o novo entra por escrita atomica. Se a execucao quebra, entra um FAILED com
    `INFRA:RUNNER_ERROR`: uma falha nunca deixa um sucesso antigo no lugar. Falha atribuivel ao executor vai tambem
    para `<out>/failures.jsonl`. Usado pela CLI e pela fila de producao."""
    target = Path(out) / dir_name
    target.mkdir(parents=True, exist_ok=True)
    path = target / "outcome.json"
    if path.exists():
        n = 1
        while (target / f"outcome.{n}.json").exists():
            n += 1
        os.replace(path, target / f"outcome.{n}.json")
    started = wall()
    try:
        outcome = execute()
    except Exception as exc:                                                 # noqa: BLE001 - vira desfecho, nao some
        traceback.print_exc(file=sys.stderr)
        j = job if isinstance(job, dict) else {}
        outcome = {"schema": OUTCOME_SCHEMA, "job_id": j.get("job_id"), "task_type": j.get("task_type"),
                   "status": "FAILED", "error": f"INFRA:RUNNER_ERROR: {type(exc).__name__}", "executor_id": None,
                   "tokens_measured": None, "tokens_estimated": None, "cloud": {"tokens": 0, "providers_called": []},
                   "assistance": [], "permissions": [], "failure": None, "started_at": started, "finished_at": wall()}
        if isinstance(j.get("attempt"), int):
            outcome["attempt"] = j["attempt"]
    tmp = target / "outcome.json.tmp"
    tmp.write_text(json.dumps(outcome, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
    if outcome.get("failure"):
        with open(Path(out) / "failures.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(outcome["failure"], ensure_ascii=False, default=str) + "\n")
    return outcome, path


def main(argv: list[str] | None = None) -> int:
    from agent_bar.platform.sketchup_sample import executor_adapter, git_head, sample_tool_from_env
    ap = argparse.ArgumentParser(description="executa um job p2s.local_job.v1 e grava o outcome")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("job")
    r.add_argument("--out", required=True)
    r.add_argument("--endpoint", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    r.add_argument("--timeout", type=float, default=3600.0)
    args = ap.parse_args(argv)
    raw = Path(args.job).read_bytes()
    try:
        job = json.loads(raw.decode("utf-8"))
    except ValueError:
        job = None
    tool = sample_tool_from_env()

    def build_host():
        from agent_bar.platform.render_sample import RenderSampleTool, ToolBox
        return ToolBox([tool, RenderSampleTool(tool)])
    job_sha = hashlib.sha256(raw).hexdigest()
    jid = job["job_id"] if isinstance(job, dict) and isinstance(job.get("job_id"), str) and _JOB_ID.match(job["job_id"]) \
        else f"invalid-{job_sha[:12]}"

    def execute() -> dict:
        res = run_job(job, build_host=build_host, local_adapter=lambda: executor_adapter(args.endpoint),
                      engine_root=tool.engine_root, production=os.environ.get("P2S_PRODUCTION_ENGINE"),
                      head=git_head, timeout_s=args.timeout, endpoint=args.endpoint)
        return {**res, "job_sha256": job_sha}
    outcome, path = record_outcome(Path(args.out).resolve(), jid, job, execute)
    print(json.dumps({"job_id": jid, "status": outcome["status"], "outcome": str(path)}, ensure_ascii=False))
    return 0 if outcome["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
