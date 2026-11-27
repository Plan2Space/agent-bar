"""LAB-16 pos-merge (prova de 2026-11-27): o llama3.1:8b mandou `cameras` como STRING com JSON dentro
(`"[\"amostra_perspectiva\", \"amostra_lateral\"]"`), nao como lista, e o render foi recusado.

Coercao ESTRITA (decisao do coordenador):
- so para parametros declarados `array` no schema publicado;
- so se a string decodificar como JSON para uma lista que passa no MESMO schema (tipo dos itens, enum, minItems,
  maxItems, uniqueItems);
- qualquer outra coisa continua recusada;
- o cartao e o registro guardam que houve coercao (valor cru + valor usado).
E mais dois achados da mesma prova: a recusa do render saia com o titulo/tool do build, e a recusa nao guardava os
argumentos que o modelo mandou."""
from __future__ import annotations

import json

import pytest

from agent_bar.platform.local_tools import coerce_json_array_args
from agent_bar.platform.render_sample import RENDER_TOOL
from agent_bar.platform.sketchup_sample import TOOL_NAME, summary
from tests.platform.test_render_sample import (  # noqa: F401
    CAMS,
    FakeEngine,
    Perm,
    built,
    call,
    ctx,
    engine_root,
    tools,
)

LLAMA_CAMERAS = json.dumps(CAMS)                     # exatamente o que o llama mandou: '["amostra_perspectiva", ...]'
SCHEMA = {"type": "object", "properties": {
    "run_id": {"type": "string"},
    "cameras": {"type": "array", "items": {"type": "string", "enum": CAMS + ["amostra_topo"]}, "minItems": 1,
                "maxItems": 2, "uniqueItems": True}}}


# ------------------------------------------------------------------------------------------- (1) coercao estrita
def test_a_json_string_for_an_array_param_runs_the_render_with_the_decoded_list(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    perm = Perm()
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": LLAMA_CAMERAS}), ctx(perm, "r1"))
    assert res["status"] == "completed", res
    argv = engine.calls[-1]
    assert argv[argv.index("--cameras") + 1: argv.index("--slot")] == CAMS
    coerced = [{"param": "cameras", "raw": LLAMA_CAMERAS, "used": CAMS}]
    assert res["argument_coercion"] == coerced                                 # no registro (resultado gravado)
    assert perm.requests[0].tool_call["rawInput"]["argument_coercion"] == coerced   # no cartao do pedido
    stored = json.loads(build.registry.get(res["request_id"])["result"])
    assert stored["argument_coercion"] == coerced
    assert "coercao" in summary(res) and LLAMA_CAMERAS in summary(res)       # o cartao final diz o cru e o usado


def test_a_list_sent_as_a_list_records_no_coercion(engine_root, tmp_path):
    build, render = tools(engine_root, tmp_path, FakeEngine())
    b = built(build)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS}), ctx(Perm(), "r1"))
    assert res["status"] == "completed" and res["argument_coercion"] == []
    assert "coercao" not in summary(res)


@pytest.mark.parametrize("raw", [
    "amostra_perspectiva, amostra_lateral",                                 # string que nao e JSON
    '{"cameras": ["amostra_perspectiva"]}',                                 # JSON que vira objeto
    '"amostra_perspectiva"',                                                # JSON que vira string
    '["camera_inventada"]',                                                 # lista com camara fora da spec
    json.dumps(CAMS + ["amostra_topo"]),                                    # mais de 2 camaras
    json.dumps(CAMS[:1] * 2),                                               # camara repetida
    "[]",                                                                   # lista vazia
    "[1, 2]",                                                               # itens que nao sao string
    '["amostra_perspectiva"',                                               # JSON cortado
])
def test_anything_else_stays_refused_without_asking(engine_root, tmp_path, raw):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    n, perm = len(engine.calls), Perm()
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": raw}), ctx(perm, "r1"))
    assert res["status"] == "refused" and res["error"]["code"] == "CAMERA_NOT_ALLOWED"
    assert perm.requests == [] and len(engine.calls) == n
    assert res.get("argument_coercion", []) == []                            # nada foi coagido


@pytest.mark.parametrize("raw", [
    "amostra_perspectiva, amostra_lateral",                                 # nao e JSON
    '{"amostra_perspectiva": 1}',                                           # JSON objeto (nem pelas chaves)
    '["camera_inventada"]',                                                 # fora do enum
    json.dumps(CAMS + ["amostra_topo"]),                                    # acima de maxItems
    json.dumps(CAMS[:1] * 2),                                               # repetida (uniqueItems)
    "[]",                                                                   # abaixo de minItems
    "[1]",                                                                  # item nao string
])
def test_the_coercion_itself_refuses_what_the_schema_refuses(raw):
    """Defesa em profundidade: a coercao nao depende do validador da ferramenta para ser estrita."""
    args, done = coerce_json_array_args({"cameras": raw}, SCHEMA)
    assert args == {"cameras": raw} and done == []


def test_only_params_declared_array_are_ever_coerced():
    args, done = coerce_json_array_args({"run_id": '["lx-1"]', "cameras": LLAMA_CAMERAS}, SCHEMA)
    assert args["run_id"] == '["lx-1"]'                                     # string no schema: fica como veio
    assert args["cameras"] == CAMS and done == [{"param": "cameras", "raw": LLAMA_CAMERAS, "used": CAMS}]
    odd = {"properties": {"tag": {"type": "string", "items": {"type": "string"}}}}   # tem items, mas nao e array
    assert coerce_json_array_args({"tag": '["a"]'}, odd) == ({"tag": '["a"]'}, [])


def test_coercion_needs_a_schema_it_can_check():
    loose = {"properties": {"xs": {"type": "array"}}}                       # sem `items`: nao da para conferir
    args, done = coerce_json_array_args({"xs": '["a"]'}, loose)
    assert args == {"xs": '["a"]'} and done == []
    obj_items = {"properties": {"xs": {"type": "array", "items": {"type": "object"}}}}
    args, done = coerce_json_array_args({"xs": '[{"a": 1}]'}, obj_items)
    assert args == {"xs": '[{"a": 1}]'} and done == []
    strings = {"properties": {"xs": {"type": "array", "items": {"type": "string"}}}}   # sem enum: so o tipo barra
    assert coerce_json_array_args({"xs": "[1, true]"}, strings) == ({"xs": "[1, true]"}, [])
    assert coerce_json_array_args({"xs": '["a"]'}, strings)[1] == [{"param": "xs", "raw": '["a"]', "used": ["a"]}]
    any_items = {"properties": {"xs": {"type": "array", "items": {}}}}      # items sem tipo: nao confere
    assert coerce_json_array_args({"xs": '["a"]'}, any_items) == ({"xs": '["a"]'}, [])


def test_coercion_never_touches_the_original_arguments():
    original = {"cameras": LLAMA_CAMERAS}
    args, _ = coerce_json_array_args(original, SCHEMA)
    assert original == {"cameras": LLAMA_CAMERAS} and args is not original


def test_the_published_schema_says_cameras_are_distinct(engine_root, tmp_path):
    _, render = tools(engine_root, tmp_path, FakeEngine())
    cams = render.specs()[0]["function"]["parameters"]["properties"]["cameras"]
    assert cams["type"] == "array" and cams["uniqueItems"] is True and cams["maxItems"] == 2


# ------------------------------------------------------------------------- (2) recusa do render com o nome certo
def test_a_refused_render_card_is_titled_with_the_render_tool(engine_root, tmp_path):
    build, render = tools(engine_root, tmp_path, FakeEngine())
    b = built(build)
    c = ctx(Perm(), "r1")
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": ["camera_inventada"]}), c)
    assert res["tool"] == RENDER_TOOL
    updates = [d for k, d in c.events if k == "tool_update"]
    assert updates and updates[-1]["title"] == f"Executor local (amostra): {RENDER_TOOL}"
    assert TOOL_NAME not in updates[-1]["title"]


def test_a_refused_build_card_keeps_the_build_title(engine_root, tmp_path):
    build, _ = tools(engine_root, tmp_path, FakeEngine())
    c = ctx(Perm(), "b9")
    res = build.run(call(TOOL_NAME, {"fixture_ref": "nao_existe"}), c)
    assert res["status"] == "refused" and res["tool"] == TOOL_NAME
    assert [d for k, d in c.events if k == "tool_update"][-1]["title"] == f"Executor local (amostra): {TOOL_NAME}"


# ---------------------------------------------------------------- (3) a recusa guarda os argumentos do modelo
def test_a_refusal_keeps_what_the_model_sent(engine_root, tmp_path):
    build, render = tools(engine_root, tmp_path, FakeEngine())
    b = built(build)
    c = ctx(Perm(), "r1")
    sent = {"run_id": b["run_id"], "cameras": "amostra_perspectiva, amostra_lateral"}
    res = render.run(call(RENDER_TOOL, sent), c)
    assert res["model_arguments"] == sent
    card = [d for k, d in c.events if k == "tool_update"][-1]["content"][0]["content"]["text"]
    assert "amostra_perspectiva, amostra_lateral" in card and "argumentos do modelo" in card


def test_a_refused_build_keeps_what_the_model_sent(engine_root, tmp_path):
    build, _ = tools(engine_root, tmp_path, FakeEngine())
    res = build.run(call(TOOL_NAME, {"fixture_ref": "../../segredo"}), ctx(Perm(), "b9"))
    assert res["status"] == "refused" and res["model_arguments"] == {"fixture_ref": "../../segredo"}
