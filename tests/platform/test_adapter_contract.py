"""Suite de contrato comum: o adaptador de referencia passa; cada adaptador defeituoso reprova na checagem certa."""
from __future__ import annotations

import pytest

from agent_bar.platform.adapter import CapabilityUnsupported, ProviderAdapter, SessionRequest
from tests.platform.adapter_contract import CHECKS, ContractContext, run_contract
from tests.platform.fake_adapter import DEFECTIVE, FakeAdapter

FAST = ContractContext(prompt_timeout=10.0, cancel_timeout=2.0, connect_timeout=10.0)


def test_reference_adapter_passes_every_check():
    assert run_contract(lambda env: FakeAdapter(env=env), FAST) == {}


@pytest.mark.parametrize("cls", list(DEFECTIVE), ids=lambda c: c.__name__)
def test_each_defective_adapter_fails_the_check_that_guards_its_rule(cls):
    """Autoteste do harness: se um defeito conhecido passar, a suite esta quebrada (nao o adaptador)."""
    expected = DEFECTIVE[cls]
    made = []

    def factory(env):
        made.append(cls(env=env))
        return made[-1]
    try:
        failures = run_contract(factory, FAST, only=(expected,))
    finally:
        for a in made:                                                       # o OrphanProcess deixa o filho vivo de proposito
            orphan = getattr(a, "_orphan", None)
            if orphan is not None and orphan.poll() is None:
                orphan.kill()
                orphan.wait(timeout=10)
    assert expected in failures, f"{cls.__name__} passou em {expected}: a suite nao pega este defeito"


def test_every_check_is_exercised_by_at_least_one_defect_or_is_structural():
    guarded = set(DEFECTIVE.values())
    structural = {"models", "session_record", "prompt_round_trip", "resume"}  # cobertas pelo adaptador de referencia
    assert set(CHECKS) <= guarded | structural


def test_base_class_refuses_capability_operations_instead_of_no_op():
    class Minimal(ProviderAdapter):
        adapter_id = "minimal"

        def describe(self): return {}
        def connect(self, timeout=30.0): pass
        def disconnect(self): pass
        def get_status(self): return None
        def list_models(self): return []
        def create_session(self, request): return {}
        def send_prompt(self, session_id, text, on_event): return None

    m = Minimal()
    for call, cap in ((lambda: m.resume_session("x"), "resume"), (lambda: m.cancel("x"), "cancel"),
                      (lambda: m.list_effort_levels("x"), "effort_select")):
        with pytest.raises(CapabilityUnsupported) as exc:
            call()
        assert exc.value.capability == cap and exc.value.code == "CAPABILITY_UNSUPPORTED"
    assert m.owned_pids() == [] and m.process_env({"GH_TOKEN": "x"}) is None


def test_requested_and_accepted_are_recorded_separately():
    a = FakeAdapter(spawn=False)
    a.connect()
    try:
        rec = a.create_session(SessionRequest(model_id="fake-think", effort="high"))
        assert (rec["model_requested"], rec["model_accepted"]) == ("fake-think", "fake-think")
        assert (rec["effort_requested"], rec["effort_accepted"]) == ("high", "high")
        default = a.create_session(SessionRequest())
        assert default["model_requested"] is None and default["model_accepted"] == "fake-small"
    finally:
        a.disconnect()
