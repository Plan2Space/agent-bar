"""compat_bench.py — benchmark progressivo de compatibilidade modelo x motor (pedido do Felipe 2026-11-27 §8-§11).

O que se mede: se um modelo local (Llama, DeepSeek, Qwen... pelo Ollama, que e PROVEDOR) consegue OPERAR o mesmo motor
(sketchup-mcp-next), pelas mesmas ferramentas do Studio e na mesma versao. Nao se mede se o modelo "gera geometria": a
geometria, a validacao, o .skp e o render sao do motor (§8).

Regras:
- mesmo caminho do produto: `executor_adapter` (OllamaAdapter + ToolBox do Executor local), as MESMAS ferramentas,
  descricoes e system prompt para todos os modelos; a unica diferenca entre execucoes e o `model_id`;
- condicoes iguais e registradas: commits do Studio e do motor (com dirty), versao do Ollama, digest do modelo, sha256
  das ferramentas publicadas e do system prompt, opcoes de inferencia fixas (temperatura e seed) e o modo de permissao;
- cada caso roda numa conversa NOVA; o texto dos prompts e fixo e vai no registro;
- sem Claude como fallback: nenhum caminho deste modulo chama outro provedor. Qualquer ajuda externa fica em
  `assistance` (lista vazia = nenhuma), e o caso que precisou dela nao conta como sucesso autonomo;
- eixos separados (operacao x planejamento x criacao de layout), sem nota unica;
- falha do motor ou da maquina (SketchUp ocupado, sem manifest, prazo) e INFRA_ERROR, nunca FAIL do modelo;
- caso que depende de ferramenta que o motor ainda nao publica fica BLOCKED com o motivo e o dono (Frente A).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_bar.platform.adapter import AgentEvent, SessionRequest

SCHEMA = "p2s.mcp_compat.run.v1"
AXES = ("operation", "planning", "layout")
STATUSES = ("PASS", "FAIL", "INFRA_ERROR", "BLOCKED", "NOT_RUN", "NOT_SUPPORTED")
INFERENCE = {"temperature": 0, "seed": 42}                                   # iguais para todos os modelos
PERMISSION = "auto allow_once pelo runner (equivale ao modo bypass do produto); igual para todos os modelos"
# erros que vem do que o MODELO mandou (argumento, nome, repeticao); o resto e do motor/maquina/provedor
MODEL_CODES = frozenset({"UNKNOWN_TOOL", "MALFORMED", "DUPLICATE", "TURN_LIMIT", "ARGUMENT_NOT_ALLOWED",
                         "FIXTURE_NOT_ALLOWED", "RUN_NOT_RENDERABLE", "CAMERA_NOT_ALLOWED"})
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


# ------------------------------------------------------------------------------------------------ registro
class RecordingHost:
    """Envolve o tool_host do Studio SEM mudar nada: so anota cada chamada que chega ao executor e o resultado."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.calls: list[dict] = []

    def __getattr__(self, name: str) -> Any:                                 # system_prompt, model_view, final_text...
        return getattr(self.inner, name)

    def specs(self) -> list[dict]:
        return self.inner.specs()

    def names(self) -> set[str]:
        return self.inner.names()

    def run(self, call, ctx) -> dict:
        res = self.inner.run(call, ctx)
        self.calls.append({"name": call.name, "arguments": dict(call.arguments), "raw": dict(call.raw), "result": res})
        return res


@dataclass
class Trace:
    """O que aconteceu num caso: turnos (prompt, texto do modelo, eventos), chamadas que chegaram ao executor e recusas
    da validacao do Studio antes do executor."""
    turns: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)

    @property
    def model_text(self) -> str:
        return "\n".join(t["model_text"] for t in self.turns)


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    axis: str
    prompts: tuple[str, ...] = ()
    check: Callable[[Trace, dict], tuple[str, list[str]]] | None = None
    blocked: str | None = None                                               # motivo de BLOCKED (falta no motor)
    owner: str | None = None                                                 # quem desbloqueia
    allow_tools: bool = True                                                 # False: permissao negada (nada abre)


# ------------------------------------------------------------------------------------------------ criterios
def _infra(call: dict) -> str | None:
    """Codigo de falha que nao e do modelo (motor, maquina, provedor), ou None."""
    err = (call.get("result") or {}).get("error") or {}
    code = err.get("code")
    return code if code and code not in MODEL_CODES else None


def check_c0(trace: Trace, ctx: dict) -> tuple[str, list[str]]:
    """C0 descoberta: o modelo lista as ferramentas publicadas, sem chamar nenhuma e sem inventar outra."""
    published = set(ctx["tools"])
    if trace.calls or trace.refusals:
        return "FAIL", ["chamou ferramenta em vez de listar"]
    found = set(_SNAKE.findall(trace.model_text))
    known = published | set(ctx.get("vocabulary") or ())                    # fixtures e parametros publicados
    missing = sorted(published - found)
    invented = sorted(found - known)
    reasons = [f"faltou: {missing}"] if missing else []
    reasons += [f"nome fora do publicado: {invented}"] if invented else []
    return ("PASS" if not reasons else "FAIL"), reasons or [f"listou {sorted(published)}"]


def _single_build(trace: Trace, fixture: str) -> tuple[str, list[str]]:
    if trace.refusals:
        return "FAIL", [f"chamada recusada pelo Studio: {r['code']}" for r in trace.refusals]
    if not trace.calls:
        return "FAIL", ["nenhuma chamada de ferramenta"]
    if len(trace.calls) > 1:
        return "FAIL", [f"{len(trace.calls)} chamadas para um pedido de uma"]
    c = trace.calls[0]
    res = c["result"] or {}
    if c["name"] != "build_room_sample":
        return "FAIL", [f"ferramenta errada: {c['name']}"]
    if c["arguments"] != {"fixture_ref": fixture}:
        code = (res.get("error") or {}).get("code")
        return "FAIL", [f"argumentos {c['arguments']} (esperado fixture_ref={fixture})" + (f"; {code}" if code else "")]
    infra = _infra(c)
    if infra:
        return "INFRA_ERROR", [f"chamada certa, motor/maquina falhou: {infra}"]
    if res.get("status") != "completed" or (res.get("inspection_result") or {}).get("verdict") != "PASS":
        return "INFRA_ERROR", [f"chamada certa, resultado do motor {res.get('status')}"]
    return "PASS", [f"{fixture} -> {res.get('run_id')} (.skp {str(res.get('skp_sha256'))[:12]}, inspecao PASS)"]


def check_c1(trace: Trace, ctx: dict) -> tuple[str, list[str]]:
    """C1 chamada simples: a ferramenta e o argumento estao no pedido; o modelo so precisa chamar certo."""
    return _single_build(trace, "room_001")


def check_c2(trace: Trace, ctx: dict) -> tuple[str, list[str]]:
    """C2 construcao existente: o pedido descreve a sala; o modelo escolhe a fixture certa pela descricao publicada."""
    return _single_build(trace, "plan_alt_5x3_e3a")


ENGINE_GAP = "o motor ainda nao publica a ferramenta necessaria (matriz §9, Frente A)"
CASES: tuple[Case, ...] = (
    Case("C0", "Descoberta", "operation",
         ("Sem chamar nenhuma ferramenta, liste os nomes exatos das ferramentas que voce pode chamar agora, um por "
          "linha.",), check_c0, allow_tools=False),
    Case("C1", "Chamada simples", "operation",
         ("Chame a ferramenta build_room_sample com fixture_ref igual a room_001.",), check_c1),
    Case("C2", "Construcao existente", "operation",
         ("Construa no SketchUp a sala de 5 x 3 metros com cama.",), check_c2),
    Case("C3", "Parametrizacao", "operation", blocked=f"{ENGINE_GAP}: construcao com parametros novos "
         "(dimensoes/aberturas) validados pelo motor", owner="Frente A"),
    Case("C4", "Operacoes sequenciais", "planning"),
    Case("C5", "Recuperacao de erro", "operation"),
    Case("C6", "Renderizacao", "operation"),
    Case("C7", "Refinamento", "planning", blocked=f"{ENGINE_GAP}: alterar um build existente (editar em copia "
         "nova)", owner="Frente A"),
    Case("C8", "Fluxo completo ate evidencia", "planning", blocked="nao ha ferramenta publicada para publicar "
         "evidencia (Evidence e da Frente C)", owner="Frente A + Frente C"),
    Case("C9", "Suite 01 a partir de spec autoritativa", "layout", blocked=f"{ENGINE_GAP}: construir a Suite 01 a "
         "partir da spec", owner="Frente A"),
)


# ------------------------------------------------------------------------------------------------ execucao
def _event(ev: AgentEvent) -> dict:
    return {"kind": ev.kind, "text": ev.text, "data": json.loads(json.dumps(dict(ev.data or {}), default=str))}


def _sha(obj: Any) -> str:
    raw = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_turn(adapter: Any, sid: str, prompt: str, timeout_s: float, clock: Callable[[], float]) -> dict:
    events: list[AgentEvent] = []
    t0 = clock()
    try:
        res = adapter.send_prompt(sid, prompt, events.append).result(timeout_s)
        stop, confirmed, error = res.stop_reason, res.model_confirmed, None
    except Exception as exc:                                                  # noqa: BLE001 - vira registro
        stop, confirmed, error = "error", None, f"{type(exc).__name__}: {str(exc)[:300]}"
    studio = [e.text for e in events if e.kind == "text" and e.text.startswith("Resultado do motor (escrito pelo Studio")]
    model_text = "".join(e.text for e in events if e.kind == "text" and e.text not in studio)
    usage = next((dict(e.data) for e in reversed(events) if e.kind == "usage"), None)
    return {"prompt": prompt, "stop_reason": stop, "model_confirmed": confirmed, "error": error,
            "latency_s": round(clock() - t0, 3), "model_text": model_text, "studio_text": "\n".join(studio),
            "thought_chars": sum(len(e.text) for e in events if e.kind == "thought"), "usage": usage,
            "events": [_event(e) for e in events]}


def _refusals(turns: list[dict]) -> list[dict]:
    out = []
    for t in turns:
        for e in t["events"]:
            title = str(e["data"].get("title") or "")
            if e["kind"] == "tool_update" and title.startswith("ferramenta recusada"):
                text = ((e["data"].get("content") or [{}])[0].get("content") or {}).get("text", "")
                out.append({"code": text.split(":", 1)[0], "detail": text, "raw": e["data"].get("rawInput")})
    return out


def run_case(adapter: Any, host: RecordingHost, model: str, case: Case, ctx: dict, *, timeout_s: float,
             clock: Callable[[], float]) -> dict:
    base = {"id": case.id, "title": case.title, "axis": case.axis, "prompts": list(case.prompts),
            "assistance": []}
    if case.blocked:
        return {**base, "status": "BLOCKED", "reasons": [case.blocked], "owner": case.owner}
    if case.check is None:
        return {**base, "status": "NOT_RUN", "reasons": ["runner ainda nao implementa este caso"]}
    if ctx["model_tools"] != "SUPPORTED":
        return {**base, "status": "NOT_SUPPORTED", "reasons": [f"{model} nao anuncia tools no /api/show"]}
    n = len(host.calls)
    sess = adapter.create_session(SessionRequest(model_id=model, workspace=ctx.get("workspace")))
    sid = sess["session_id"]
    try:
        turns = [run_turn(adapter, sid, p, timeout_s, clock) for p in case.prompts]
    finally:
        adapter.close_session(sid)
    trace = Trace(turns=turns, calls=host.calls[n:], refusals=_refusals(turns))
    if any(t["error"] for t in turns):
        status, reasons = "INFRA_ERROR", [t["error"] for t in turns if t["error"]]
    else:
        status, reasons = case.check(trace, ctx)
    return {**base, "status": status, "reasons": reasons, "turns": turns,
            "tool_calls": [{k: c[k] for k in ("name", "arguments", "raw")} | {"result": _brief(c["result"])}
                           for c in trace.calls],
            "refusals": trace.refusals}


def _brief(res: dict) -> dict:
    keys = ("tool", "status", "request_id", "run_id", "fixture_ref", "skp_sha256", "error", "engine_commit",
            "engine_dirty", "studio_commit", "studio_dirty", "model", "model_digest", "argument_coercion",
            "model_arguments", "images", "render_verdict")
    out = {k: res.get(k) for k in keys if k in res}
    if "inspection_result" in res:
        out["inspection_verdict"] = (res.get("inspection_result") or {}).get("verdict")
    return out


def conditions(adapter: Any, host: RecordingHost, model: str) -> dict:
    specs = host.specs()
    system = host.system_prompt() if hasattr(host.inner, "system_prompt") else None
    names = sorted(host.names())
    vocab = set()
    for s in specs:
        params = (s.get("function") or {}).get("parameters") or {}
        for pname, p in (params.get("properties") or {}).items():
            vocab.add(pname)
            vocab.update(v for v in (p.get("enum") or (p.get("items") or {}).get("enum") or []) if isinstance(v, str))
    info = adapter._models.get(model) or {}
    return {"tools": names, "tools_sha256": _sha(specs), "system_prompt_sha256": _sha(system) if system else None,
            "vocabulary": sorted(vocab), "inference": dict(adapter.inference_options),
            "permission": PERMISSION, "model_tools": info.get("tools"), "workspace": None}


def versions(adapter: Any, host: RecordingHost, model: str) -> dict:
    from agent_bar.platform.sketchup_sample import git_head
    tools = getattr(host.inner, "tools", None) or [host.inner]
    build = tools[0]
    studio = git_head(Path(__file__).resolve().parents[2]) or {}
    engine = git_head(build.engine_root) or {}
    try:
        digest = adapter.model_digest(model)
    except Exception:                                                         # noqa: BLE001 - modelo sem digest
        digest = None
    return {"studio": {"repo": "Plan2Space/agent-bar", **studio},
            "engine": {"repo": "Plan2Space/sketchup-mcp-next", "root": str(build.engine_root), **engine},
            "provider": {"id": "ollama", "version": adapter.version, "endpoint": adapter.endpoint},
            "model": {"id": model, "digest": digest}}


def axes_view(cases: list[dict]) -> dict:
    """Resultado por eixo, sem nota unica: contagem por status e os casos de cada eixo."""
    out = {}
    for axis in AXES:
        mine = [c for c in cases if c["axis"] == axis]
        out[axis] = {"cases": [c["id"] for c in mine],
                     "by_status": {s: sum(c["status"] == s for c in mine) for s in STATUSES if any(
                         c["status"] == s for c in mine)}}
    return out


def run_model(adapter: Any, host: RecordingHost, model: str, case_ids: list[str], *, timeout_s: float = 1800.0,
              clock: Callable[[], float] = time.monotonic, wall: Callable[[], str] | None = None,
              machine: Callable[[], dict] | None = None) -> dict:
    wall = wall or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    machine = machine or (lambda: {})
    perms: list[dict] = []
    current: dict[str, Case] = {}

    def on_permission(req, respond) -> None:
        tc = dict(getattr(req, "tool_call", {}) or {})
        case = current.get("case")
        choice = "allow_once" if case is None or case.allow_tools else "reject_once"
        perms.append({"at": wall(), "case": case.id if case else None, "title": tc.get("title"), "decided": choice,
                      "decided_by": "runner"})
        respond(choice)
    adapter.on_permission = on_permission
    ctx = conditions(adapter, host, model)
    run = {"schema": SCHEMA, "started_at": wall(), "versions": versions(adapter, host, model), "conditions": ctx,
           "assistance": [], "machine_before": machine(), "cases": []}
    for case in CASES:
        if case.id not in case_ids:
            continue
        current["case"] = case
        rec = run_case(adapter, host, model, case, ctx, timeout_s=timeout_s, clock=clock)
        run["cases"].append(rec)
    run["permissions"] = perms
    run["machine_after"] = machine()
    run["finished_at"] = wall()
    run["axes"] = axes_view(run["cases"])
    return run


# ------------------------------------------------------------------------------------------------ CLI
def _machine() -> dict:
    """GPU (nvidia-smi), espaco livre e segundos sem entrada do usuario — so leitura."""
    import ctypes
    import shutil
    import subprocess
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader"], capture_output=True, timeout=20).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        gpu = None
    idle = None
    if os.name == "nt":
        class LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = LII(ctypes.sizeof(LII), 0)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            idle = (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) // 1000
    disks = {d: shutil.disk_usage(d).free // 2**20 for d in ("C:\\", "E:\\") if os.path.exists(d)} if os.name == "nt" \
        else {}
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "gpu": gpu, "idle_s": idle, "free_mb": disks}


def main(argv: list[str] | None = None) -> int:
    from agent_bar.platform.sketchup_sample import executor_adapter
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", action="append", required=True, help="model_id do Ollama (repetir para varios)")
    ap.add_argument("--cases", default="C0,C1,C2")
    ap.add_argument("--out", required=True, help="pasta de saida (uma subpasta por modelo, nunca sobrescreve)")
    ap.add_argument("--endpoint", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    ap.add_argument("--timeout", type=float, default=1800.0)
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    code = 0
    for model in args.model:
        adapter = executor_adapter(args.endpoint)
        adapter.inference_options = dict(INFERENCE)
        host = RecordingHost(adapter.tool_host)
        adapter.tool_host = host
        adapter.connect()
        try:
            run = run_model(adapter, host, model, cases, timeout_s=args.timeout, machine=_machine)
        finally:
            adapter.disconnect()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target = out / f"bench-{stamp}-{re.sub(r'[^A-Za-z0-9._-]', '_', model)}"
        target.mkdir(parents=True, exist_ok=False)
        (target / "benchmark_run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str),
                                                   encoding="utf-8")
        line = {c["id"]: c["status"] for c in run["cases"]}
        print(json.dumps({"model": model, "out": str(target), "cases": line}, ensure_ascii=False), flush=True)
        code |= any(s == "INFRA_ERROR" for s in line.values())
    return code


if __name__ == "__main__":
    sys.exit(main())
