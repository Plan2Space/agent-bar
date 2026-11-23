"""F6 (WORKSPACE-UX): telemetria com estado por campo. Estimativa nunca aparece como medicao; o que o provedor nao
informa e UNAVAILABLE (nunca zero); o maximo do modelo nao e a janela; custo equivalente de API nao e fatura."""
from __future__ import annotations

import pytest

from agent_bar.platform.usage import (
    Metric,
    UsageSnapshot,
    from_acp_usage_update,
    from_ollama_done,
    usage_line,
)

OLLAMA_DONE = {"model": "llama3.1:8b", "done": True, "done_reason": "stop", "prompt_eval_count": 13,
               "prompt_eval_cached_count": 0, "eval_count": 2}                # chunk real medido em 2026-11-23


def ollama(msg=OLLAMA_DONE, **kw):
    return from_ollama_done(msg, provider_id="ollama", model_id="llama3.1:8b", model_max_context=131072, **kw)


# ------------------------------------------------------------------------------------------------ invariantes
@pytest.mark.parametrize("value,state", [(0, "UNAVAILABLE"), (None, "MEASURED"), (None, "ESTIMATED"), (None, "STALE"),
                                         (1, "GUESSED")])
def test_metric_refuses_inconsistent_states(value, state):
    with pytest.raises(ValueError):
        Metric(value, state)


def test_snapshot_round_trips_and_refuses_another_schema():
    s = ollama()
    assert UsageSnapshot.from_dict(s.to_dict()) == s
    with pytest.raises(ValueError):
        UsageSnapshot.from_dict({**s.to_dict(), "schema": "usage.v0"})


# ------------------------------------------------------------------------------------------------ Ollama
def test_ollama_turn_counts_are_measured_and_context_is_only_estimated():
    s = ollama()
    assert (s.input_tokens.value, s.input_tokens.state) == (13, "MEASURED")
    assert (s.output_tokens.value, s.output_tokens.state) == (2, "MEASURED")
    assert (s.context_used.value, s.context_used.state) == (15, "ESTIMATED")
    assert s.context_window.state == "UNAVAILABLE"                           # num_ctx em uso nao e informado
    assert (s.model_max_context.value, s.model_max_context.state) == (131072, "MEASURED")
    assert s.cost_api_equivalent_usd.state == "UNAVAILABLE"


def test_ollama_window_is_measured_only_when_the_studio_fixed_num_ctx():
    s = ollama(num_ctx=8192)
    assert (s.context_window.value, s.context_window.state) == (8192, "MEASURED")


def test_ollama_without_counters_is_unavailable_never_zero():
    s = ollama({"model": "x", "done": True})
    assert all(getattr(s, n).state == "UNAVAILABLE" for n in ("input_tokens", "output_tokens", "context_used"))
    assert usage_line(s) == "contexto não informado pelo provedor"


def test_ollama_line_says_estimated_and_that_the_window_is_unknown():
    line = usage_line(ollama())
    assert line == ("contexto 15 (estimado), janela não informada · máx. do modelo 131k · "
                    "turno 13 entrada / 2 saída (medido)")


# ------------------------------------------------------------------------------------------------ ACP
def test_acp_used_and_size_are_measured_and_cost_is_an_api_equivalent_estimate():
    s = from_acp_usage_update({"used": 41950, "size": 200000, "cost": {"amount": 0.0312, "currency": "USD"}},
                              provider_id="claude-acp", model_id="default")
    assert (s.context_used.state, s.context_window.state, s.cost_api_equivalent_usd.state) == (
        "MEASURED", "MEASURED", "ESTIMATED")
    assert s.input_tokens.state == "UNAVAILABLE"                               # o usage_update nao traz contadores
    assert usage_line(s) == ("contexto 42k/200k (medido) · custo equivalente de API US$ 0.03 "
                             "(estimado, não é fatura)")


@pytest.mark.parametrize("data", [{"used": 10, "size": 100, "cost": {"amount": 1, "currency": "EUR"}},
                                  {"used": 10, "size": 100, "cost": {"amount": -1, "currency": "USD"}},
                                  {"used": "10", "size": None}])
def test_acp_odd_fields_become_unavailable(data):
    s = from_acp_usage_update(data, provider_id="p", model_id="m")
    assert s.cost_api_equivalent_usd.state == "UNAVAILABLE"
    if data["used"] == "10":
        assert s.context_used.state == "UNAVAILABLE" and s.context_window.state == "UNAVAILABLE"


# ------------------------------------------------------------------------------------------------ atualizacao
def test_a_field_missing_from_the_next_update_keeps_its_value_as_stale():
    first = from_acp_usage_update({"used": 100, "size": 1000, "cost": {"amount": 0.5, "currency": "USD"}},
                                  provider_id="p", model_id="m")
    later = first.merged_with(from_acp_usage_update({"used": 200, "size": 1000}, provider_id="p", model_id="m"))
    assert (later.context_used.value, later.context_used.state) == (200, "MEASURED")
    assert (later.cost_api_equivalent_usd.value, later.cost_api_equivalent_usd.state) == (0.5, "STALE")


def test_another_model_replaces_instead_of_merging():
    a = from_acp_usage_update({"used": 100, "size": 1000, "cost": {"amount": 0.5, "currency": "USD"}},
                              provider_id="p", model_id="m1")
    b = a.merged_with(from_acp_usage_update({"used": 5, "size": 10}, provider_id="p", model_id="m2"))
    assert b.cost_api_equivalent_usd.state == "UNAVAILABLE"


def test_stale_marks_known_values_and_says_why():
    s = ollama().stale("configuração trocada: aguardando nova medição")
    assert s.input_tokens.state == "STALE" and s.context_window.state == "UNAVAILABLE"
    assert usage_line(s).endswith("(desatualizado) · configuração trocada: aguardando nova medição")


def test_nothing_reported_yet():
    assert usage_line(None) == "uso: não informado pelo provedor"
