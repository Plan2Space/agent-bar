"""adapter_contract.py — SUITE DE CONTRATO COMUM (SPEC §5 F1, harness H2). Todo adaptador passa por aqui antes da UI.

Uso num teste de adaptador:

    from tests.platform.adapter_contract import ContractContext, run_contract
    assert run_contract(lambda env: MeuAdaptador(env=env)) == {}

`run_contract` devolve {nome_da_checagem: motivo} so com as que falharam. Cada checagem recebe um adaptador NOVO
(fabrica chamada com um ambiente que contem segredos-canario) e sempre o desconecta no fim. O autoteste
(`test_adapter_contract.py`) prova que cada defeito conhecido reprova em pelo menos uma checagem: suite que aprova
adaptador defeituoso e suite quebrada.

Adaptador real passa pela mesma suite so com `-m real_agent` (custa tokens; nunca no CI de PR).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from agent_bar.platform.adapter import (
    EVENT_KINDS,
    AdapterError,
    AgentEvent,
    CapabilityUnsupported,
    EffortRejected,
    ModelRejected,
    NotConnected,
    PromptResult,
    ProviderAdapter,
    SessionNotFound,
    SessionRequest,
)
from agent_bar.platform.contracts import (
    CAPABILITY_STATES,
    STOP_REASONS,
    ContractError,
    validate_model,
    validate_provider,
    validate_session,
)
from tests.support.process import pid_alive

BOGUS_MODEL = "__p2s_contract_no_such_model__"
BOGUS_EFFORT = "__p2s_contract_no_such_effort__"
CANARY = {                                                                   # valores que nunca podem reaparecer
    "ANTHROPIC_API_KEY": "canary-anthropic-3f9c1e",
    "OPENAI_API_KEY": "canary-openai-77ab20",
    "GH_TOKEN": "canary-gh-0d41aa",
    "P2S_FAKE_SECRET": "canary-generic-b6e5f2",
}

Factory = Callable[[Mapping[str, str]], ProviderAdapter]


@dataclass
class ContractContext:
    prompt: str = "Responda apenas: ok"
    slow_prompt: str = "Conte devagar de 1 a 200, um numero por linha."    # longo o bastante para dar tempo do cancel
    prompt_timeout: float = 30.0
    cancel_timeout: float = 10.0
    connect_timeout: float = 30.0
    base_env: dict[str, str] = field(default_factory=lambda: {"PATH": "C:\\Windows", "SYSTEMROOT": "C:\\Windows", **CANARY})


class ContractFailure(AssertionError):
    pass


def _fail(msg: str) -> None:
    raise ContractFailure(msg)


def _caps(adapter: ProviderAdapter) -> dict[str, str]:
    return {k: v["state"] for k, v in adapter.describe()["capabilities"].items()}


def _collect(adapter: ProviderAdapter, sid: str, text: str, timeout: float) -> tuple[PromptResult, list[AgentEvent]]:
    events: list[AgentEvent] = []
    fut = adapter.send_prompt(sid, text, events.append)
    if not isinstance(fut, cf.Future):
        _fail(f"send_prompt devolveu {type(fut).__name__}, nao Future")
    try:
        res = fut.result(timeout=timeout)
    except cf.TimeoutError:
        _fail(f"prompt nao terminou em {timeout}s")
    return res, events


def _check_result(res: object, events: list[AgentEvent]) -> None:
    if not isinstance(res, PromptResult) or res.stop_reason not in STOP_REASONS:
        _fail(f"resultado fora do contrato: {res!r}")
    for ev in events:
        if not isinstance(ev, AgentEvent) or ev.kind not in EVENT_KINDS:
            _fail(f"evento fora do contrato: {ev!r}")


# ------------------------------------------------------------------------------------------------------ checagens
def check_provider_record(a: ProviderAdapter, ctx: ContractContext) -> None:
    rec = a.describe()
    try:
        validate_provider(rec)
    except ContractError as exc:
        _fail(f"Provider v1 invalido: {exc}")
    if rec["adapter_id"] != a.adapter_id:
        _fail(f"adapter_id {rec['adapter_id']!r} != {a.adapter_id!r}")
    if rec["connection_status"] != "CONNECTED":
        _fail(f"conectado mas describe() diz {rec['connection_status']}")
    for name, cap in rec["capabilities"].items():
        if cap["state"] == "SUPPORTED" and not cap["evidence"]["method"].strip():
            _fail(f"{name} SUPPORTED sem evidencia")


def check_unsupported_raise_typed(a: ProviderAdapter, ctx: ContractContext) -> None:
    caps = _caps(a)
    sid = a.create_session(SessionRequest())["session_id"]
    probes = {
        "resume": lambda: a.resume_session(sid),
        "cancel": lambda: a.cancel(sid),
        "effort_select": lambda: a.list_effort_levels(None),
    }
    for name, call in probes.items():
        if caps[name] != "UNSUPPORTED":
            continue
        try:
            got = call()
        except CapabilityUnsupported as exc:
            if exc.capability != name:
                _fail(f"{name}: CapabilityUnsupported com capacidade {exc.capability!r}")
            continue
        except Exception as exc:                                             # noqa: BLE001
            _fail(f"{name} UNSUPPORTED levantou {type(exc).__name__}, nao CapabilityUnsupported")
        _fail(f"{name} UNSUPPORTED virou no-op silencioso (devolveu {got!r})")
    if caps["model_select"] == "UNSUPPORTED":
        try:
            a.create_session(SessionRequest(model_id=BOGUS_MODEL))
        except (CapabilityUnsupported, ModelRejected):
            pass
        else:
            _fail("model_select UNSUPPORTED aceitou modelo explicito")


def check_models(a: ProviderAdapter, ctx: ContractContext) -> None:
    models = a.list_models()
    if not isinstance(models, list):
        _fail(f"list_models devolveu {type(models).__name__}")
    pid = a.describe()["provider_id"]
    ids = []
    for m in models:
        try:
            validate_model(m)
        except ContractError as exc:
            _fail(f"Model v1 invalido: {exc}")
        if m["provider_id"] != pid:
            _fail(f"modelo {m['model_id']} com provider_id {m['provider_id']!r}")
        ids.append(m["model_id"])
    if len(ids) != len(set(ids)):
        _fail("model_id repetido")
    if _caps(a)["model_select"] == "SUPPORTED" and not models:
        _fail("model_select SUPPORTED com lista vazia")


def check_unknown_model_rejected(a: ProviderAdapter, ctx: ContractContext) -> None:
    if _caps(a)["model_select"] != "SUPPORTED":
        return
    try:
        rec = a.create_session(SessionRequest(model_id=BOGUS_MODEL))
    except ModelRejected:
        return
    _fail(f"modelo inexistente aceito: {rec.get('model_accepted')!r}")


def check_effort_honest(a: ProviderAdapter, ctx: ContractContext) -> None:
    if _caps(a)["effort_select"] != "SUPPORTED":
        return
    models = a.list_models()
    for m in models:
        levels = a.list_effort_levels(m["model_id"])
        if list(levels) != list(m["effort_levels"]):
            _fail(f"{m['model_id']}: list_effort_levels {levels} != Model.effort_levels {m['effort_levels']}")
        try:
            rec = a.create_session(SessionRequest(model_id=m["model_id"], effort=BOGUS_EFFORT))
        except EffortRejected:
            pass
        else:
            _fail(f"{m['model_id']}: esforco inexistente aceito ({rec.get('effort_accepted')!r})")
        for level in levels:
            rec = a.create_session(SessionRequest(model_id=m["model_id"], effort=level))
            _close(a, rec["session_id"])                                     # o claude-agent-acp cai com ~75 sessoes abertas
            if rec["effort_accepted"] not in (None, level):
                _fail(f"{m['model_id']}: pediu {level}, aceito {rec['effort_accepted']!r}")


def _close(a: ProviderAdapter, sid: str) -> None:
    """Fecha a sessao que a checagem abriu, quando o adaptador sabe fechar (a checagem `close_session` cobra o resto)."""
    try:
        a.close_session(sid)
    except CapabilityUnsupported:
        pass


def check_close_session(a: ProviderAdapter, ctx: ContractContext) -> None:
    sid = a.create_session(SessionRequest())["session_id"]
    try:
        a.close_session(sid)
    except CapabilityUnsupported as exc:
        if exc.capability != "close_session":
            _fail(f"close_session recusou com capacidade {exc.capability!r}")
        return                                                               # nao fecha: recusa explicita, nao no-op
    if sid in a.get_status().sessions:
        _fail("sessao fechada continua em get_status().sessions")
    try:
        a.send_prompt(sid, ctx.prompt, lambda e: None)
    except SessionNotFound:
        pass
    else:
        _fail("prompt numa sessao fechada foi aceito")
    try:
        a.close_session(sid)
    except SessionNotFound:
        return
    _fail("fechar de novo uma sessao fechada nao levantou SessionNotFound")


def check_session_record(a: ProviderAdapter, ctx: ContractContext) -> None:
    req = SessionRequest(workspace="C:\\contract\\ws", links={"issue": "Plan2Space/agent-bar#44"})
    rec = a.create_session(req)
    try:
        validate_session(rec)
    except ContractError as exc:
        _fail(f"Session v1 invalido: {exc}")
    if rec["provider_id"] != a.describe()["provider_id"]:
        _fail("sessao com provider_id de outro provedor")
    if rec["workspace"] != req.workspace or rec["links"]["issue"] != "Plan2Space/agent-bar#44":
        _fail("workspace/vinculos da requisicao nao preservados")
    listed = {m["model_id"] for m in a.list_models()}
    if rec["model_accepted"] is not None and listed and rec["model_accepted"] not in listed:
        _fail(f"model_accepted {rec['model_accepted']!r} nao esta em list_models")
    if rec["model_requested"] is not None:
        _fail("sessao sem modelo pedido registrou model_requested")
    if rec["session_id"] not in a.get_status().sessions:
        _fail("sessao criada nao aparece em get_status().sessions")


def check_prompt_round_trip(a: ProviderAdapter, ctx: ContractContext) -> None:
    sid = a.create_session(SessionRequest())["session_id"]
    res, events = _collect(a, sid, ctx.prompt, ctx.prompt_timeout)
    _check_result(res, events)
    if res.stop_reason != "end_turn":
        _fail(f"prompt simples terminou com {res.stop_reason}")
    if not any(e.kind == "text" and e.text for e in events):
        _fail("prompt terminou sem nenhum texto")


def check_cancel(a: ProviderAdapter, ctx: ContractContext) -> None:
    if _caps(a)["cancel"] != "SUPPORTED":
        return
    sid = a.create_session(SessionRequest())["session_id"]
    first = threading.Event()
    events: list[AgentEvent] = []

    def sink(ev: AgentEvent) -> None:
        events.append(ev)
        first.set()
    fut = a.send_prompt(sid, ctx.slow_prompt, sink)
    first.wait(ctx.prompt_timeout)
    if fut.done():
        _fail("prompt lento terminou antes do cancel (contexto mal configurado)")
    t0 = time.monotonic()
    a.cancel(sid)
    try:
        res = fut.result(timeout=ctx.cancel_timeout)
    except cf.TimeoutError:
        _fail(f"cancel aceito mas o prompt continuou por mais de {ctx.cancel_timeout}s (no-op silencioso)")
    _check_result(res, events)
    if res.stop_reason != "cancelled":
        _fail(f"depois do cancel o stop_reason foi {res.stop_reason}")
    if time.monotonic() - t0 > ctx.cancel_timeout:
        _fail("cancel lento demais")


def check_resume(a: ProviderAdapter, ctx: ContractContext) -> None:
    if _caps(a)["resume"] != "SUPPORTED":
        return
    sid = a.create_session(SessionRequest())["session_id"]
    rec = a.resume_session(sid)
    validate_session(rec)
    if rec["session_id"] != sid:
        _fail("resume devolveu outra sessao")
    try:
        a.resume_session("__p2s_contract_no_such_session__")
    except SessionNotFound:
        return
    _fail("resume de sessao inexistente nao levantou SessionNotFound")


def check_disconnect(a: ProviderAdapter, ctx: ContractContext) -> None:
    a.create_session(SessionRequest())
    pids = list(a.owned_pids())
    a.disconnect()
    try:
        a.disconnect()
    except Exception as exc:                                                 # noqa: BLE001
        _fail(f"disconnect nao e idempotente: {type(exc).__name__}")
    if a.get_status().connection_status != "DISCONNECTED":
        _fail(f"depois do disconnect: {a.get_status().connection_status}")
    end = time.monotonic() + 5
    while time.monotonic() < end and any(pid_alive(p) for p in pids):
        time.sleep(0.05)
    alive = [p for p in pids if pid_alive(p)]
    if alive:
        _fail(f"processos do adaptador sobreviveram ao disconnect: {alive}")
    try:
        a.create_session(SessionRequest())
    except NotConnected:
        return
    except AdapterError as exc:
        _fail(f"operacao depois do disconnect levantou {exc.code}, nao NOT_CONNECTED")
    _fail("operacao depois do disconnect funcionou")


def check_no_secret_leak(a: ProviderAdapter, ctx: ContractContext) -> None:
    child = a.process_env(ctx.base_env)
    if child is not None:
        leaked = sorted(k for k, v in child.items() if v in CANARY.values())
        if leaked:
            _fail(f"segredos chegariam ao processo filho: {leaked}")
    sid = a.create_session(SessionRequest())["session_id"]
    res, events = _collect(a, sid, ctx.prompt, ctx.prompt_timeout)
    blob = json.dumps({
        "provider": a.describe(), "models": a.list_models(), "status": repr(a.get_status()),
        "session": a.resume_session(sid) if _caps(a)["resume"] == "SUPPORTED" else None,
        "events": [repr(e) for e in events], "result": repr(res),
    }, default=repr)
    found = [k for k, v in CANARY.items() if v in blob]
    if found:
        _fail(f"segredo do ambiente apareceu na saida do adaptador: {found}")


CHECKS: dict[str, Callable[[ProviderAdapter, ContractContext], None]] = {
    "provider_record": check_provider_record,
    "unsupported_raise_typed": check_unsupported_raise_typed,
    "models": check_models,
    "unknown_model_rejected": check_unknown_model_rejected,
    "effort_honest": check_effort_honest,
    "session_record": check_session_record,
    "prompt_round_trip": check_prompt_round_trip,
    "cancel": check_cancel,
    "resume": check_resume,
    "disconnect": check_disconnect,
    "close_session": check_close_session,
    "no_secret_leak": check_no_secret_leak,
}


def run_contract(factory: Factory, ctx: ContractContext | None = None, only: tuple[str, ...] = ()) -> dict[str, str]:
    ctx = ctx or ContractContext()
    failures: dict[str, str] = {}
    for name, check in CHECKS.items():
        if only and name not in only:
            continue
        adapter = factory(dict(ctx.base_env))
        try:
            adapter.connect(timeout=ctx.connect_timeout)
            check(adapter, ctx)
        except ContractFailure as exc:
            failures[name] = str(exc)
        except Exception as exc:                                             # noqa: BLE001 - qualquer outra coisa tambem reprova
            failures[name] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                adapter.disconnect()
            except Exception as exc:                                         # noqa: BLE001
                failures.setdefault(name, f"disconnect final falhou: {type(exc).__name__}: {exc}")
    return failures


assert set(CAPABILITY_STATES) >= {"SUPPORTED", "UNSUPPORTED"}
