"""Provider/Capability/Model/Session v1: arquivos publicados == schema do codigo; validacao estrita e fail-closed."""
from __future__ import annotations

import copy

import pytest

from agent_bar.platform import contracts as c
from agent_bar.platform.discovery import discover
from tests.platform.fake_adapter import FakeAdapter


@pytest.mark.parametrize("name", list(c.SCHEMAS))
def test_published_schema_files_match_the_code(name):
    path = c.SCHEMA_DIR / f"{name}.v1.json"
    assert path.read_text(encoding="utf-8") == c.schema_text(name), "rode: python -m agent_bar.platform.contracts"


def _provider():
    return copy.deepcopy(discover(which=lambda n: f"C:\\bin\\{n}.cmd", env={})[0])


def test_a_valid_provider_passes_and_unknown_capability_state_is_the_default():
    rec = c.validate_provider(_provider())
    assert all(v["state"] == "UNKNOWN" for v in rec["capabilities"].values())


@pytest.mark.parametrize("mutate,where", [
    (lambda r: r.pop("cost_class"), "cost_class"),
    (lambda r: r.update(provider_type="SSH"), "provider_type"),
    (lambda r: r.update(auth_status="LOGGED"), "auth_status"),
    (lambda r: r["capabilities"].pop("cancel"), "capabilities.cancel"),
    (lambda r: r["capabilities"]["cancel"].update(state="YES"), "capabilities.cancel.state"),
    (lambda r: r["capabilities"]["cancel"]["evidence"].pop("method"), "capabilities.cancel.evidence.method"),
    (lambda r: r.update(token="x"), ""),                                     # campo extra (ex.: segredo) e recusado
    (lambda r: r.update(schema="p2s.provider.v2"), "schema"),
    (lambda r: r.update(executable_path=None), "executable_path"),
])
def test_provider_outside_the_contract_is_refused_with_the_field(mutate, where):
    rec = _provider()
    mutate(rec)
    with pytest.raises(c.ContractError) as exc:
        c.validate_provider(rec)
    assert exc.value.path == where


def test_http_provider_needs_an_endpoint():
    rec = next(p for p in discover(which=lambda n: None, env={}) if p["provider_type"] == "LOCAL_HTTP")
    rec = dict(rec, endpoint=None)
    with pytest.raises(c.ContractError, match="endpoint"):
        c.validate_provider(rec)


def test_model_and_session_records_validate_and_reject_bad_types():
    a = FakeAdapter(spawn=False)
    a.connect()
    try:
        model = a.list_models()[1]
        c.validate_model(model)
        bad = dict(model, limits={"context_tokens": "8k"})
        with pytest.raises(c.ContractError, match="limits.context_tokens"):
            c.validate_model(bad)
        with pytest.raises(c.ContractError, match="context_tokens"):
            c.validate_model(dict(model, limits={"context_tokens": True}))  # bool nao e inteiro
        from agent_bar.platform.adapter import SessionRequest
        sess = a.create_session(SessionRequest())
        c.validate_session(sess)
        with pytest.raises(c.ContractError, match="state"):
            c.validate_session(dict(sess, state="DONE"))
    finally:
        a.disconnect()


def test_all_capabilities_refuses_an_unknown_name():
    with pytest.raises(c.ContractError):
        c.all_capabilities(teleport=c.capability("SUPPORTED", "x"))


@pytest.mark.parametrize("value, schema, ok", [
    (1.5, {"type": "number"}, True), (True, {"type": "number"}, False), ("1", {"type": "number"}, False),
    ("abc", {"type": "string", "pattern": "^[a-c]+$"}, True), ("abd", {"pattern": "^[a-c]+$"}, False),
    (5, {"minimum": 0, "maximum": 5}, True), (6, {"maximum": 5}, False), (-1, {"minimum": 0}, False),
    ([1], {"minItems": 1, "maxItems": 2}, True), ([], {"minItems": 1}, False), ([1, 2, 3], {"maxItems": 2}, False),
    (["a", "b"], {"uniqueItems": True}, True), (["a", "a"], {"uniqueItems": True}, False),
    ({"a": 1}, {"oneOf": [{"required": ["a"]}, {"required": ["b"]}]}, True),
    ({"a": 1, "b": 2}, {"oneOf": [{"required": ["a"]}, {"required": ["b"]}]}, False),   # casa com as duas
    ({}, {"oneOf": [{"required": ["a"]}, {"required": ["b"]}]}, False),                 # casa com nenhuma
])
def test_validate_covers_the_keywords_used_by_the_local_job_contracts(value, schema, ok):
    if ok:
        c.validate(value, schema)
    else:
        with pytest.raises(c.ContractError):
            c.validate(value, schema)
