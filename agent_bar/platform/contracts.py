"""contracts.py — Provider, Capability, Model e Session v1 (SPEC §4), como schemas JSON e validacao estrita.

Os schemas sao a fonte; os arquivos publicados em `agent_bar/platform/schemas/*.v1.json` sao gerados daqui e um teste
garante que nao divergem. Mudanca incompativel = schema v2 + migracao, nunca editar o v1 em silencio.

Validador proprio (subconjunto de JSON Schema: type, enum, required, properties, additionalProperties, items, const)
para nao trazer dependencia nova ao produto.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PROVIDER_TYPES = ("ACP", "CLI", "HTTP_API", "LOCAL_HTTP")
AUTH_STATUSES = ("AUTHENTICATED", "AUTH_REQUIRED", "UNKNOWN", "UNSUPPORTED", "ERROR")
CONNECTION_STATUSES = ("NOT_FOUND", "DISCONNECTED", "CONNECTING", "CONNECTED", "ERROR")
COST_CLASSES = ("local", "subscription_cli", "paid_api")
CAPABILITY_NAMES = ("streaming", "sessions", "resume", "tool_calls", "cancel", "model_select", "effort_select", "images")
CAPABILITY_STATES = ("SUPPORTED", "UNSUPPORTED", "UNKNOWN", "ERROR")
MODEL_AVAILABILITY = ("AVAILABLE", "UNAVAILABLE", "UNKNOWN")
SESSION_STATES = ("CREATING", "READY", "RUNNING", "CANCELLING", "CLOSED", "ERROR")
STOP_REASONS = ("end_turn", "cancelled", "max_tokens", "max_turn_requests", "refusal", "error")

_STR = {"type": "string"}
_STR_OR_NULL = {"type": ["string", "null"]}

CAPABILITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["state", "evidence"],
    "additionalProperties": False,
    "properties": {
        "state": {"enum": list(CAPABILITY_STATES)},
        "evidence": {                                                        # como se sabe: versao do provedor + metodo
            "type": "object",
            "required": ["version", "method"],
            "additionalProperties": False,
            "properties": {"version": _STR_OR_NULL, "method": _STR},
        },
    },
}

PROVIDER_SCHEMA: dict[str, Any] = {
    "$id": "p2s.provider.v1",
    "type": "object",
    "required": ["schema", "provider_id", "display_name", "provider_type", "adapter_id", "executable_path", "endpoint",
                 "version", "auth_status", "connection_status", "capabilities", "discovery_source", "last_verified_at",
                 "cost_class"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": "p2s.provider.v1"},
        "provider_id": _STR,
        "display_name": _STR,
        "provider_type": {"enum": list(PROVIDER_TYPES)},
        "adapter_id": _STR,
        "executable_path": _STR_OR_NULL,
        "endpoint": _STR_OR_NULL,
        "version": _STR_OR_NULL,
        "auth_status": {"enum": list(AUTH_STATUSES)},
        "connection_status": {"enum": list(CONNECTION_STATUSES)},
        "capabilities": {
            "type": "object",
            "required": list(CAPABILITY_NAMES),
            "additionalProperties": False,
            "properties": {name: CAPABILITY_SCHEMA for name in CAPABILITY_NAMES},
        },
        "discovery_source": _STR,
        "last_verified_at": _STR_OR_NULL,
        "cost_class": {"enum": list(COST_CLASSES)},
    },
}

MODEL_SCHEMA: dict[str, Any] = {
    "$id": "p2s.model.v1",
    "type": "object",
    "required": ["schema", "model_id", "display_name", "provider_id", "transport", "availability", "tools", "images",
                 "effort_levels", "limits", "source"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": "p2s.model.v1"},
        "model_id": _STR,                                                    # id EXATO enviado ao provedor
        "display_name": _STR,                                                # o que a UI mostra
        "provider_id": _STR,
        "transport": {"enum": list(PROVIDER_TYPES)},
        "availability": {"enum": list(MODEL_AVAILABILITY)},
        "tools": {"enum": list(CAPABILITY_STATES)},
        "images": {"enum": list(CAPABILITY_STATES)},
        "effort_levels": {"type": "array", "items": _STR},                   # [] = este modelo nao aceita esforco
        "limits": {
            "type": "object",
            "required": ["context_tokens"],
            "additionalProperties": False,
            "properties": {"context_tokens": {"type": ["integer", "null"]}},
        },
        "source": _STR,                                                      # de onde veio (ex.: "GET /api/tags")
    },
}

SESSION_SCHEMA: dict[str, Any] = {
    "$id": "p2s.session.v1",
    "type": "object",
    "required": ["schema", "session_id", "provider_id", "model_requested", "model_accepted", "effort_requested",
                 "effort_accepted", "workspace", "links", "state", "created_at"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": "p2s.session.v1"},
        "session_id": _STR,
        "provider_id": _STR,
        "model_requested": _STR_OR_NULL,
        "model_accepted": _STR_OR_NULL,                                      # so o que o provedor confirmou; null = nao confirmado
        "effort_requested": _STR_OR_NULL,
        "effort_accepted": _STR_OR_NULL,
        "workspace": _STR_OR_NULL,
        "links": {
            "type": "object",
            "required": ["spec", "issue", "pr", "run"],
            "additionalProperties": False,
            "properties": {k: _STR_OR_NULL for k in ("spec", "issue", "pr", "run")},
        },
        "state": {"enum": list(SESSION_STATES)},
        "created_at": _STR,
    },
}

SCHEMAS = {"provider": PROVIDER_SCHEMA, "model": MODEL_SCHEMA, "session": SESSION_SCHEMA}
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


class ContractError(ValueError):
    """Registro fora do contrato. `path` aponta o campo (ex.: `capabilities.cancel.state`)."""

    def __init__(self, path: str, message: str):
        super().__init__(f"{path or '<raiz>'}: {message}")
        self.path = path


_TYPES = {"string": str, "object": dict, "array": list, "null": type(None), "boolean": bool}


def _type_ok(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def validate(value: Any, schema: Mapping[str, Any], path: str = "") -> None:
    """Levanta ContractError no primeiro desvio. Nao converte nem completa nada."""
    if "const" in schema and value != schema["const"]:
        raise ContractError(path, f"esperado {schema['const']!r}, veio {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(path, f"{value!r} fora de {schema['enum']}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            raise ContractError(path, f"tipo {type(value).__name__} nao e {types}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ContractError(f"{path}.{key}".lstrip("."), "campo obrigatorio ausente")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(props))
            if extra:
                raise ContractError(path, f"campos fora do contrato: {extra}")
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, f"{path}.{key}".lstrip("."))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")


def validate_provider(record: Any) -> dict:
    validate(record, PROVIDER_SCHEMA)
    if record["provider_type"] in ("ACP", "CLI") and not record["executable_path"] and record["connection_status"] != "NOT_FOUND":
        raise ContractError("executable_path", "provedor de processo encontrado sem executavel")
    if record["provider_type"] in ("HTTP_API", "LOCAL_HTTP") and not record["endpoint"]:
        raise ContractError("endpoint", "provedor HTTP sem endpoint")
    return record


def validate_model(record: Any) -> dict:
    validate(record, MODEL_SCHEMA)
    return record


def validate_session(record: Any) -> dict:
    validate(record, SESSION_SCHEMA)
    return record


def capability(state: str, method: str, version: str | None = None) -> dict:
    return {"state": state, "evidence": {"version": version, "method": method}}


def all_capabilities(default_state: str = "UNKNOWN", method: str = "not verified", version: str | None = None,
                     **overrides: dict) -> dict:
    """Mapa completo das 8 capacidades: o que nao foi verificado fica UNKNOWN (nunca SUPPORTED por padrao)."""
    caps = {name: capability(default_state, method, version) for name in CAPABILITY_NAMES}
    for name, value in overrides.items():
        if name not in caps:
            raise ContractError(f"capabilities.{name}", "capacidade desconhecida")
        caps[name] = value
    return caps


def schema_text(name: str) -> str:
    return json.dumps(SCHEMAS[name], indent=2, ensure_ascii=False) + "\n"


def write_schema_files(directory: Path = SCHEMA_DIR) -> list[Path]:
    """Regenera os arquivos publicados (`python -m agent_bar.platform.contracts`)."""
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name in SCHEMAS:
        path = directory / f"{name}.v1.json"
        path.write_text(schema_text(name), encoding="utf-8", newline="\n")
        out.append(path)
    return out


if __name__ == "__main__":
    for p in write_schema_files():
        print(p)
