"""Pré-requisito do S01C-F1-05: transporte, registro e uso da imagem LIGADOS, de ponta a ponta.

Um único caminho, sem espião: SessionManager (com o ImageLedger) → send_with_images → OllamaAdapter real → servidor
HTTP do Ollama falso (que, como o Ollama de verdade, conta os tokens da imagem no `prompt_eval_count`). Prova que:
  1. a imagem chega ao provedor (base64 no último turno);
  2. a entrega foi gravada no ledger ANTES do envio e terminou SENT, com o sha256 do arquivo e a sessão do Studio;
  3. o evento de uso do mesmo turno mede os tokens de entrada, e eles incluem a imagem (a diferença contra o mesmo
     texto sem imagem é exatamente o que o provedor contou para a imagem).
O teste real opt-in (`AGENT_BAR_REAL_VISION_E2E=<modelo>`) faz o mesmo contra o Ollama local.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest

from agent_bar.platform.adapter import AgentEvent, SessionRequest
from agent_bar.platform.image_ledger import ImageLedger, ReferenceLink
from agent_bar.platform.ollama import OllamaAdapter
from agent_bar.platform.sessions import SessionManager
from agent_bar.platform.usage import UsageSnapshot
from tests.platform.fake_ollama import IMAGE_TOKENS, FakeOllama

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000d49444154789c63f8cfc0f01f00050001ffa59b8cd50000000049454e44ae426082")


def _turn(sm: SessionManager, sid: str, text: str, images=()) -> tuple[list[AgentEvent], UsageSnapshot]:
    ev: list[AgentEvent] = []
    sm.send(sid, text, ev.append, images=images, purpose="EVALUATION", requested_by="studio:review",
            reference=ReferenceLink("ref-r2", reference_version="r2")).result(30)
    [u] = [e for e in ev if e.kind == "usage"]
    return ev, UsageSnapshot.from_dict(u.data)


def _manager(endpoint: str, ledger: ImageLedger, tmp_path: Path) -> SessionManager:
    return SessionManager({"ollama": lambda **kw: OllamaAdapter(endpoint, env=kw.get("env"))},
                          store_path=tmp_path / "sessions.json", image_ledger=ledger)


def test_image_is_sent_recorded_in_the_ledger_and_counted_in_usage(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(PNG)
    sha = hashlib.sha256(PNG).hexdigest()
    with FakeOllama() as srv:
        ledger = ImageLedger(tmp_path / "permissions.sqlite")
        sm = _manager(srv.endpoint, ledger, tmp_path)
        try:
            sid = sm.open("ollama", SessionRequest(model_id="vision-d"))["id"]
            _, plain = _turn(sm, sid, "descreva a cama")
            assert ledger.list() == []                                       # sem imagem: nada no ledger
            ev, with_image = _turn(sm, sid, "descreva a cama", images=[img])

            # 1. transporte: a imagem chegou ao provedor, em base64, só no último turno
            chats = [b for p, b in srv.requests if p == "/api/chat"]
            assert chats[-1]["messages"][-1]["images"] == [base64.b64encode(PNG).decode()]
            assert "imagens=1" in "".join(e.text for e in ev if e.kind == "text")

            # 2. registro: gravado e marcado SENT, ligado à sessão do Studio, ao modelo e à referência
            [row] = ledger.list()
            assert (row.sha256, row.status, row.session_id, row.provider_id, row.model_id) == (
                sha, "SENT", sid, "ollama", "vision-d")
            assert (row.purpose, row.requested_by, row.ref_id, row.ref_version) == (
                "EVALUATION", "studio:review", "ref-r2", "r2")

            # 3. uso: tokens de entrada MEDIDOS no mesmo turno, e a imagem está contada neles
            assert with_image.input_tokens.state == "MEASURED" and plain.input_tokens.state == "MEASURED"
            assert with_image.input_tokens.value - plain.input_tokens.value == IMAGE_TOKENS
            assert (with_image.provider_id, with_image.model_id) == ("ollama", "vision-d")
        finally:
            sm.close_all()
            ledger.close()


def test_model_without_vision_leaves_a_not_sent_row_and_no_usage(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(PNG)
    with FakeOllama() as srv:
        ledger = ImageLedger(tmp_path / "permissions.sqlite")
        sm = _manager(srv.endpoint, ledger, tmp_path)
        try:
            sid = sm.open("ollama", SessionRequest(model_id="chat-a"))["id"]
            ev: list[AgentEvent] = []
            with pytest.raises(Exception):
                sm.send(sid, "descreva", ev.append, images=[img]).result(30)
            [row] = ledger.list()
            assert row.status == "NOT_SENT" and row.error
            assert not any(e.kind == "usage" for e in ev)                    # nada foi contado: nada foi enviado
            assert not any(b["messages"][-1].get("images") for p, b in srv.requests if p == "/api/chat")
        finally:
            sm.close_all()
            ledger.close()


REAL = os.environ.get("AGENT_BAR_REAL_VISION_E2E")                            # ex.: qwen2.5vl:7b;<imagem>


@pytest.mark.skipif(not REAL, reason="opt-in: AGENT_BAR_REAL_VISION_E2E=<modelo com vision>;<caminho da imagem>")
def test_real_ollama_vision_model_records_and_counts_the_image(tmp_path):
    model, path = REAL.split(";", 1)
    endpoint = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    ledger = ImageLedger(tmp_path / "permissions.sqlite")
    sm = _manager(endpoint, ledger, tmp_path)
    try:
        sid = sm.open("ollama", SessionRequest(model_id=model))["id"]
        _, plain = _turn(sm, sid, "Responda só: ok")
        sid2 = sm.open("ollama", SessionRequest(model_id=model))["id"]           # sessão nova: sem histórico
        _, with_image = _turn(sm, sid2, "Responda só: ok", images=[Path(path)])
        [row] = ledger.list()
        assert row.status == "SENT" and row.sha256 == hashlib.sha256(Path(path).read_bytes()).hexdigest()
        assert row.session_id == sid2
        assert with_image.input_tokens.state == "MEASURED"
        assert with_image.input_tokens.value > plain.input_tokens.value + 100    # a imagem custa tokens de entrada
        print(f"\nREAL {model}: sem imagem {plain.input_tokens.value} · com imagem {with_image.input_tokens.value} "
              f"tokens de entrada · ledger {row.id} {row.status} sha {row.sha256[:12]}")
    finally:
        sm.close_all()
        ledger.close()
