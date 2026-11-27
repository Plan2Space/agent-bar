"""local_tools.py — ferramentas que o Studio publica para um modelo LOCAL (P2S-LOCAL-EXECUTOR-SKETCHUP-POC §3-§5).

O modelo so PROPOE: o adaptador entrega aqui as `tool_calls` estruturadas que o Ollama devolveu no fim do turno, e o
Studio decide. Regras deste modulo (cobradas por `tests/platform/test_ollama_tools.py`):

- so o campo estruturado `message.tool_calls` conta; texto que apenas PARECE JSON de ferramenta nunca vira chamada;
- a chamada so e validada depois do turno completo (`done`): stream cortado = nenhuma chamada;
- nome fora das ferramentas publicadas, argumentos que nao sao um objeto JSON completo e chamada repetida no mesmo turno
  sao recusados com codigo tipado, sem chegar ao `ToolHost`;
- cada chamada aceita tem `digest` = sha256 de {name, arguments} canonico, ligado ao turno, ao modelo e ao digest dele.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

REFUSAL_CODES = ("UNKNOWN_TOOL", "MALFORMED", "DUPLICATE", "TURN_LIMIT", "MODEL_MISMATCH")


@dataclass(frozen=True)
class ToolCall:
    call_id: str                                                             # id do Ollama ou "<turno>-<i>"
    name: str
    arguments: Mapping[str, Any]
    digest: str                                                              # sha256 de {name, arguments} canonico
    raw: Mapping[str, Any] = field(default_factory=dict)                     # como o servidor devolveu (normalizado)


@dataclass(frozen=True)
class RefusedCall:
    call_id: str
    name: str
    code: str                                                                # um de REFUSAL_CODES
    reason: str
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    """O que o executor sabe do turno que pediu a ferramenta (proveniencia + canal de permissao + cancelamento)."""
    session_id: str
    turn: int
    turn_id: str                                                             # uuid do Studio por envio (unico entre reinicios)
    model_id: str
    model_digest: str | None
    provider_version: str | None
    workspace: str | None
    on_permission: Callable[..., None] | None
    cancel: threading.Event
    emit: Callable[[str, Mapping[str, Any]], None]                          # (kind "tool_call"|"tool_update", dados ACP)
    # LAB-04 (P2S-STUDIO-LLM-LAB-001 §5, §10): identidade do provedor e do pedido; None = nao medido, nunca inventado
    provider: str = "ollama"
    adapter: str = "ollama"
    prompt_sha256: str | None = None                                         # sha256 do texto do usuario deste envio
    inference: Mapping[str, Any] = field(default_factory=dict)               # parametros pedidos ao provedor


class ToolHost(Protocol):
    def specs(self) -> list[dict]:
        """Ferramentas no formato do Ollama (`tools` do /api/chat)."""

    def names(self) -> set[str]: ...

    def run(self, call: ToolCall, ctx: ToolContext) -> dict:
        """Executa (ou recusa) UMA chamada ja normalizada; devolve o resultado que volta ao modelo como papel `tool`."""


def call_digest(name: str, arguments: Mapping[str, Any]) -> str:
    canon = json.dumps({"name": name, "arguments": arguments}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _arguments(raw: Any) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):                                                 # alguns servidores mandam texto JSON
        try:
            val = json.loads(raw)
        except ValueError:
            return None                                                      # JSON parcial/fragmento: recusa
        return dict(val) if isinstance(val, Mapping) else None
    return None


def normalize_tool_calls(raw_calls: Any, published: set[str], turn: int) -> tuple[list[ToolCall], list[RefusedCall]]:
    """Separa as chamadas do turno em aceitas (para o ToolHost) e recusadas (com codigo). Nunca levanta."""
    ok: list[ToolCall] = []
    refused: list[RefusedCall] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_calls if isinstance(raw_calls, list) else []):
        fn = raw.get("function") if isinstance(raw, Mapping) else None
        cid = str((raw.get("id") if isinstance(raw, Mapping) else None) or f"t{turn}-{i}")
        name = str(fn.get("name") or "") if isinstance(fn, Mapping) else ""
        shown = {"id": cid, "function": dict(fn) if isinstance(fn, Mapping) else fn}
        if not name:
            refused.append(RefusedCall(cid, "", "MALFORMED", "chamada sem function.name", shown))
            continue
        if name not in published:
            refused.append(RefusedCall(cid, name, "UNKNOWN_TOOL", f"{name!r} nao e uma ferramenta publicada", shown))
            continue
        args = _arguments(fn.get("arguments"))
        if args is None:
            refused.append(RefusedCall(cid, name, "MALFORMED", "arguments nao e um objeto JSON completo", shown))
            continue
        digest = call_digest(name, args)
        if digest in seen:
            refused.append(RefusedCall(cid, name, "DUPLICATE", "chamada repetida no mesmo turno", shown))
            continue
        seen.add(digest)
        ok.append(ToolCall(cid, name, args, digest, shown))
    return ok, refused


def _array_fits(value: list, schema: Mapping[str, Any]) -> bool:
    """A lista passa no schema publicado? So sabe conferir itens `string` (com enum opcional); o resto = nao."""
    items = schema.get("items")
    if not isinstance(items, Mapping) or items.get("type") != "string":
        return False
    if not all(isinstance(v, str) for v in value):
        return False
    if "enum" in items and not all(v in items["enum"] for v in value):
        return False
    if len(value) < int(schema.get("minItems", 0)):
        return False
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        return False
    return not (schema.get("uniqueItems") and len(set(value)) != len(value))


def coerce_json_array_args(arguments: Mapping[str, Any], parameters: Mapping[str, Any] | None
                           ) -> tuple[dict[str, Any], list[dict]]:
    """Coercao ESTRITA de um argumento `array` que o modelo mandou como STRING com JSON dentro (o llama3.1:8b faz isso
    as vezes: `"[\\"a\\", \\"b\\"]"`). So quando o parametro e declarado `array` no schema publicado E a string decodifica
    para uma lista que passa no MESMO schema; qualquer outra coisa fica como veio (e o validador da ferramenta recusa).
    Devolve (argumentos a usar, [{param, raw, used}]) sem mexer nos originais: a coercao fica registrada."""
    args = dict(arguments)
    done: list[dict] = []
    props = (parameters or {}).get("properties") or {}
    for name, schema in props.items():
        raw = args.get(name)
        if not isinstance(schema, Mapping) or schema.get("type") != "array" or not isinstance(raw, str):
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, list) and _array_fits(value, schema):
            args[name] = value
            done.append({"param": name, "raw": raw, "used": value})
    return args, done


def refusal_result(r: RefusedCall) -> dict:
    return {"status": "refused", "error": {"code": r.code, "message": r.reason}, "tool": r.name, "call_id": r.call_id}
