"""Registro obrigatorio de imagem (VISUAL-STUDIO F1; SPEC P2S-SUITE01-VISUAL-CONVERGENCE S01C-R3).

Toda imagem que o Studio entrega a um agente e registrada antes do envio; falha de persistencia e erro explicito e
nada e enviado. Os campos de referencia do Anexo A §5 cabem no registro (sem terceiro registro)."""
from __future__ import annotations

import ast
import hashlib
import sqlite3
from pathlib import Path

import pytest

from agent_bar.cockpit.notifications import NotificationCenter, image_notification
from agent_bar.platform.adapter import (
    AgentEvent,
    CapabilityUnsupported,
    ImageRejected,
    SessionRequest,
)
from agent_bar.platform.image_ledger import (
    ImageLedger,
    ImageLedgerError,
    ReferenceLink,
    send_with_images,
)
from agent_bar.platform.sessions import SessionManager
from tests.platform.fake_adapter import FakeAdapter

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000d49444154789c63f8cfc0f01f00050001ffa59b8cd50000000049454e44ae426082")
SHA = hashlib.sha256(PNG).hexdigest()


class Spy(FakeAdapter):
    """Guarda o que chegou ao provedor e o estado do registro no instante do envio."""

    def __init__(self, ledger: ImageLedger | None = None, **kw):
        super().__init__(spawn=False, **kw)
        self.ledger = ledger
        self.calls: list[dict] = []

    def send_prompt(self, session_id, text, on_event, images=()):
        seen = self.ledger.list() if self.ledger is not None else []
        self.calls.append({"images": list(images), "ledger_at_send": [(r.sha256, r.status) for r in seen]})
        return super().send_prompt(session_id, text, on_event, images=images)


@pytest.fixture
def png(tmp_path) -> Path:
    p = tmp_path / "ref.png"
    p.write_bytes(PNG)
    return p


@pytest.fixture
def ledger(tmp_path):
    led = ImageLedger(tmp_path / "permissions.sqlite", clock=lambda: 1_700_000_000.0, busy_timeout=0.1)
    yield led
    led.close()


def _open(adapter: FakeAdapter, model: str = "fake-think") -> str:
    adapter.connect()
    return adapter.create_session(SessionRequest(model_id=model))["session_id"]


def _send(adapter, ledger, sid, images, **kw):
    args = {"provider_id": "fake", "model_id": "fake-think", "requested_by": "felipe", "purpose": "REFERENCE", **kw}
    return send_with_images(adapter, ledger, sid, "descreva", lambda e: None, images, **args)


# ------------------------------------------------------------------------------------------------ caminho feliz
def test_image_is_recorded_before_it_reaches_the_provider_and_then_marked_sent(ledger, png):
    spy = Spy(ledger)
    sid = _open(spy)
    _send(spy, ledger, sid, [png], studio_session_id="p2s-abc").result(10)
    assert spy.calls[0]["ledger_at_send"] == [(SHA, "RECORDED")]               # gravado ANTES do envio
    [row] = ledger.list()
    assert (row.status, row.sha256, row.bytes, row.mime) == ("SENT", SHA, len(PNG), "image/png")
    assert (row.provider_id, row.model_id, row.session_id, row.provider_session_id) == ("fake", "fake-think", "p2s-abc", sid)
    assert (row.requested_by, row.purpose, row.recorded_at) == ("felipe", "REFERENCE", "2023-11-14T22:13:20.000+00:00")
    assert row.path == str(png.resolve())


def test_the_provider_gets_exactly_the_bytes_that_were_hashed(ledger, png):
    spy = Spy(ledger)
    sid = _open(spy)
    _send(spy, ledger, sid, [png]).result(10)
    [sent] = spy.calls[0]["images"]
    assert sent.data == PNG and sent.sha256 == ledger.list()[0].sha256      # o payload registrado, nao uma releitura


def test_a_batch_is_one_record_per_image_and_survives_reopening(tmp_path, png):
    other = tmp_path / "b.png"
    other.write_bytes(PNG + b"\x00")
    path = tmp_path / "permissions.sqlite"
    led = ImageLedger(path)
    spy = Spy(led)
    _send(spy, led, _open(spy), [png, other]).result(10)
    led.close()
    again = ImageLedger(path)
    try:
        rows = again.list()
        assert len(rows) == 2 and len({r.batch_id for r in rows}) == 1 and {r.status for r in rows} == {"SENT"}
    finally:
        again.close()


# ------------------------------------------------------------------------------------------------ S01C-R3
def test_reference_fields_of_annex_a_map_onto_the_record(ledger, png):
    ref = ReferenceLink(reference_id="ref-benchmark-suite01-r2", reference_version="r2",
                        source_type="GPT_GENERATED_ON_REQUEST", source_hash=SHA.upper(), project_id="planta_74",
                        room_id="suite_01", original_file="design-library/refs/suite01/reference-r2.webp",
                        source_manifest="design-library/refs/suite01/manifest.json", build_spec_id="c-dl03-suite01-r2",
                        created_at="2026-11-22T21:00:00Z")
    spy = Spy(ledger)
    _send(spy, ledger, _open(spy), [png], reference=ref).result(10)
    [row] = ledger.list(reference_id="ref-benchmark-suite01-r2")
    assert row.sha256 == SHA                                                   # source_hash: calculado, o informado confere
    assert (row.ref_version, row.ref_source_type, row.ref_project_id, row.ref_room_id) == (
        "r2", "GPT_GENERATED_ON_REQUEST", "planta_74", "suite_01")
    assert (row.ref_original_file, row.ref_source_manifest, row.ref_build_spec_id, row.ref_created_at) == (
        ref.original_file, ref.source_manifest, ref.build_spec_id, ref.created_at)


def test_reference_whose_source_hash_differs_from_the_file_is_refused_and_nothing_is_sent(ledger, png):
    spy = Spy(ledger)
    sid = _open(spy)
    with pytest.raises(ImageLedgerError, match="source_hash"):
        _send(spy, ledger, sid, [png], reference=ReferenceLink("ref-x", source_hash="0" * 64))
    assert spy.calls == [] and ledger.list() == []


def test_reference_without_id_is_refused(ledger, png):
    spy = Spy(ledger)
    with pytest.raises(ImageLedgerError, match="reference_id"):
        _send(spy, ledger, _open(spy), [png], reference=ReferenceLink(""))
    assert spy.calls == []


# ------------------------------------------------------------------------------------------------ falha = nada sai
def test_without_a_ledger_no_image_is_sent(png):
    spy = Spy()
    with pytest.raises(ImageLedgerError, match="sem registro"):
        _send(spy, None, _open(spy), [png])
    assert spy.calls == []


def test_closed_ledger_is_an_explicit_error_and_nothing_is_sent(ledger, png):
    spy = Spy(ledger)
    sid = _open(spy)
    ledger.close()
    with pytest.raises(ImageLedgerError, match="fechado"):
        _send(spy, ledger, sid, [png])
    assert spy.calls == []


def test_locked_database_is_an_explicit_error_and_nothing_is_sent(tmp_path, ledger, png):
    spy = Spy(ledger)
    sid = _open(spy)
    holder = sqlite3.connect(tmp_path / "permissions.sqlite", isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")                                          # outro processo segurando o banco
    try:
        with pytest.raises(ImageLedgerError, match="falha ao gravar"):
            _send(spy, ledger, sid, [png])
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert spy.calls == [] and ledger.list() == []                             # nem envio, nem linha pela metade


def test_broken_schema_is_an_explicit_error_and_nothing_is_sent(tmp_path, ledger, png):
    spy = Spy(ledger)
    sid = _open(spy)
    other = sqlite3.connect(tmp_path / "permissions.sqlite", isolation_level=None)
    other.execute("DROP TABLE image_deliveries")
    other.close()
    with pytest.raises(ImageLedgerError, match="falha ao gravar"):
        _send(spy, ledger, sid, [png])
    assert spy.calls == []


@pytest.mark.parametrize("field,value,match", [
    ("purpose", "MARKETING", "finalidade"), ("requested_by", "", "requested_by"), ("model_id", "", "model_id")])
def test_invalid_record_fields_are_refused_before_sending(ledger, png, field, value, match):
    spy = Spy(ledger)
    with pytest.raises(ImageLedgerError, match=match):
        _send(spy, ledger, _open(spy), [png], **{field: value})
    assert spy.calls == []


def test_invalid_file_is_rejected_before_anything_is_recorded(ledger, tmp_path):
    spy = Spy(ledger)
    with pytest.raises(ImageRejected):
        _send(spy, ledger, _open(spy), [tmp_path / "nao-existe.png"])
    assert spy.calls == [] and ledger.list() == []


def test_provider_refusal_leaves_the_record_as_not_sent_with_the_reason(ledger, png):
    spy = Spy(ledger)
    sid = _open(spy, model="fake-small")                                           # modelo que nao ve imagem
    with pytest.raises(CapabilityUnsupported):
        _send(spy, ledger, sid, [png], model_id="fake-small")
    [row] = ledger.list()
    assert row.status == "NOT_SENT" and row.error.startswith("CAPABILITY_UNSUPPORTED")


def test_a_failing_display_callback_does_not_undo_the_record(tmp_path, png):
    def boom(rows):
        raise RuntimeError("UI caiu")
    led = ImageLedger(tmp_path / "p.sqlite", on_change=boom)
    try:
        spy = Spy(led)
        _send(spy, led, _open(spy), [png]).result(10)
        assert [r.status for r in led.list()] == ["SENT"]
    finally:
        led.close()


# ------------------------------------------------------------------------------------------------ Session Manager
def test_session_manager_routes_images_through_the_ledger(tmp_path, png):
    led = ImageLedger(tmp_path / "p.sqlite")
    spies: list[Spy] = []

    def factory(**kw):
        spies.append(Spy(led, **{k: v for k, v in kw.items() if k == "env"}))
        return spies[-1]
    sm = SessionManager({"fake": factory}, image_ledger=led)
    try:
        sid = sm.open("fake", SessionRequest(model_id="fake-think"))["id"]
        ev: list[AgentEvent] = []
        sm.send(sid, "veja", ev.append, images=[png], purpose="EVALUATION", requested_by="studio:review").result(10)
        [row] = led.list()
        assert (row.session_id, row.purpose, row.requested_by, row.status) == (sid, "EVALUATION", "studio:review", "SENT")
        assert spies[0].calls[0]["ledger_at_send"] == [(SHA, "RECORDED")]
    finally:
        sm.close_all()
        led.close()


def test_session_manager_without_ledger_refuses_images(png):
    spies: list[Spy] = []
    sm = SessionManager({"fake": lambda **kw: spies.append(Spy()) or spies[-1]})
    sid = sm.open("fake", SessionRequest(model_id="fake-think"))["id"]
    with pytest.raises(ImageLedgerError):
        sm.send(sid, "veja", images=[png])
    assert spies[0].calls == [] and sm.get(sid)["state"] == "READY"


# ------------------------------------------------------------------------------------------------ Notificacoes
def test_each_batch_shows_up_once_in_notifications_and_follows_its_status(ledger, png):
    center = NotificationCenter()
    ledger.on_change = lambda rows: center.publish(**image_notification(rows))
    spy = Spy(ledger)
    _send(spy, ledger, _open(spy), [png], reference=ReferenceLink("ref-r2", reference_version="r2")).result(10)
    [n] = center.list(categories=["IMAGE"])
    assert n.title == "Imagem entregue: 1 arquivo(s)" and n.state == "NEW"
    assert "fake-think" in n.body and SHA[:12] in n.body and "referência ref-r2 r2" in n.body
    blind = _open(spy, model="fake-small")
    with pytest.raises(CapabilityUnsupported):
        _send(spy, ledger, blind, [png], model_id="fake-small")
    failed = [x for x in center.list(categories=["IMAGE"]) if x.state == "FAILED"]
    assert len(failed) == 1 and failed[0].title.startswith("Imagem não enviada")


# ------------------------------------------------------------------------------------------------ trava estrutural
def test_only_the_ledger_sends_images_to_an_adapter():
    """Nenhum codigo do app chama `send_prompt(..., images=...)` fora de `send_with_images` (porta unica)."""
    root = Path(__file__).resolve().parents[2] / "agent_bar"
    offenders = []
    for py in root.rglob("*.py"):
        if py.name == "image_ledger.py":
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send_prompt"
                    and (any(k.arg == "images" for k in node.keywords) or len(node.args) >= 4)):
                offenders.append(f"{py.relative_to(root)}:{node.lineno}")
    assert offenders == []


# --------------------------------------------------------------- papéis no mesmo pedido (review do #94)
def _payload(tag: bytes):
    from agent_bar.platform.adapter import ImagePayload
    data = b"\x89PNG\r\n\x1a\n" + tag
    return ImagePayload(f"{tag.decode()}.png", "image/png", data, hashlib.sha256(data).hexdigest())


def test_each_image_gets_its_role_and_the_reference_hash_is_checked_only_against_the_reference(tmp_path):
    from agent_bar.platform.image_ledger import ImageLedger, ReferenceLink
    ref, render = _payload(b"ref"), _payload(b"render")
    led = ImageLedger(tmp_path / "l.sqlite")
    try:
        rows = led.record_delivery([ref, render], provider_id="ollama", model_id="m", session_id="s",
                                   requested_by="studio:review", purpose="EVALUATION", roles=["reference", "render"],
                                   reference=ReferenceLink("ref-x", source_hash=ref.sha256))
        assert [(r.role, r.sha256) for r in rows] == [("reference", ref.sha256), ("render", render.sha256)]
        assert [r.role for r in led.batch(rows[0].batch_id)] == ["reference", "render"]
        with pytest.raises(ImageLedgerError, match="source_hash"):           # o hash da referência é o da imagem dela
            led.record_delivery([ref, render], provider_id="ollama", model_id="m", session_id="s",
                                requested_by="studio:review", purpose="EVALUATION", roles=["reference", "render"],
                                reference=ReferenceLink("ref-x", source_hash=render.sha256))
        for bad in (["reference"], ["reference", "pintura"]):
            with pytest.raises(ImageLedgerError, match="papéis"):
                led.record_delivery([ref, render], provider_id="ollama", model_id="m", session_id="s",
                                    requested_by="studio:review", purpose="EVALUATION", roles=bad)
    finally:
        led.close()


def test_a_ledger_written_before_roles_is_migrated_and_keeps_its_rows(tmp_path):
    from agent_bar.platform.image_ledger import ImageLedger
    path = tmp_path / "old.sqlite"
    led = ImageLedger(path)
    led.record_delivery([_payload(b"a")], provider_id="p", model_id="m", session_id="s", requested_by="r",
                        purpose="CHAT")
    led.close()
    con = sqlite3.connect(path)                                                   # o registro de antes: sem a coluna
    con.execute("ALTER TABLE image_deliveries DROP COLUMN role")
    con.commit()
    con.close()
    led = ImageLedger(path)
    try:
        [old] = led.list()
        assert old.role == ""
        [new] = led.record_delivery([_payload(b"b")], provider_id="p", model_id="m", session_id="s",
                                    requested_by="r", purpose="EVALUATION", roles=["render"])
        assert new.role == "render" and len(led.list()) == 2
    finally:
        led.close()
