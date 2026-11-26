"""render_sample.py — LAB-16: o modelo local pede um render V-Ray de um build SEU (P2S-STUDIO-LLM-LAB-001 §12 E4, §17).

Ferramenta `render_room_sample(run_id, cameras)`, sobre o ponto de entrada do motor `python -m p2s_engine.render`
(o mesmo `render.render_room` que o MCP `p2s-engine` publica como `render_room`, com `request.via = "mcp"`: valem as
raizes do motor, o limite de 2 camaras e as recusas SKETCHUP_BUSY/VRAY_BUSY).

Fronteiras:
- o modelo so nomeia um `run_id` de build `completed` DESTE executor cuja fixture tem render_spec na allowlist, e
  camaras que a spec tem (1 ou 2, sem repetir). `.skp`, render_spec, pasta de saida e slot sao do Studio;
- sem slot concedido (`P2S_LOCAL_EXECUTOR_RENDER_SLOT`, dado pelo coordenador) = recusa antes de perguntar;
- permissao, lock de uma execucao por processo, pedidos em voo e SketchUp aberto: os MESMOS do build (um motor por vez,
  build ou render);
- o `.skp` e re-conferido pelo sha256 gravado no build, antes e depois da permissao;
- `completed` so com render_manifest OK + veredito PASS + cada PNG dentro do run, nao vazio e com o sha256 que o motor
  registrou. Visual sempre PENDING_HUMAN. O papel `renderer` so entra quando o motor de fato produziu quadros.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_bar.platform.local_tools import ToolCall, ToolContext
from agent_bar.platform.sketchup_sample import (
    _ENGINE_LOCK,
    TOOL_NAME,
    SketchUpSampleTool,
    _error,
    _text,
    _under,
    filtered_env,
    provenance,
    sha256_file,
)

RENDER_TOOL = "render_room_sample"
RENDER_SPECS = {                                                             # fixture do build -> render_spec do motor
    "plan_alt_5x3_e3a": "tests/fixtures/render/plan_alt_5x3.sample.render.json",
}
MAX_CAMERAS = 2
_RUN_ID = re.compile(r"^lx-[0-9A-Za-z_-]+$")


def slot_from_env() -> str:
    return os.environ.get("P2S_LOCAL_EXECUTOR_RENDER_SLOT", "").strip()


class RenderSampleTool:
    def __init__(self, build: SketchUpSampleTool, *, slot: Callable[[], str] = slot_from_env,
                 render_specs: dict[str, str] | None = None):
        self.build = build
        self.slot = slot
        self.render_specs = dict(RENDER_SPECS if render_specs is None else render_specs)
        build.replay_kinds["render"] = ("render_manifest.json", self._read_for_replay, observe_renderer)

    # -------------------------------------------------------------------------------------------- publicacao
    def _spec(self, fixture_ref: str) -> Path | None:
        rel = self.render_specs.get(fixture_ref)
        if rel is None:
            return None
        p = (self.build.engine_root / rel).resolve()
        return p if _under(p, (self.build.engine_root,)) and p.is_file() else None

    def _cameras(self, spec: Path) -> list[str]:
        try:
            raw = json.loads(spec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [str(c.get("name")) for c in raw.get("cameras") or [] if isinstance(c, dict) and c.get("name")]

    def renderable_fixtures(self) -> list[str]:
        return sorted(f for f in self.render_specs if f in self.build.available_fixtures() and self._spec(f))

    def preflight(self) -> tuple[bool, str]:
        if not self.renderable_fixtures():
            return False, "nenhuma fixture com render_spec presente no motor"
        if not _under(self.build.out_root, (self.build.engine_root / "runs",)):
            return False, "a saida do executor precisa ficar sob <motor>/runs (raiz que o render do motor aceita)"
        return True, ""

    def specs(self) -> list[dict]:
        cams = sorted({c for f in self.renderable_fixtures() for c in self._cameras(self._spec(f))})
        return [{"type": "function", "function": {
            "name": RENDER_TOOL,
            "description": ("Renderiza com V-Ray um build SEU ja concluido (run_id devolvido por build_room_sample de "
                            f"uma fixture renderizavel: {self.renderable_fixtures()}), so nas camaras pedidas (1 ou 2). "
                            "So roda com o slot de SketchUp/V-Ray concedido; pede permissao antes."),
            "parameters": {"type": "object", "properties": {
                "run_id": {"type": "string", "description": "run_id de um build completed deste executor"},
                "cameras": {"type": "array", "items": {"type": "string", "enum": cams}, "minItems": 1,
                            "maxItems": MAX_CAMERAS}},
                "required": ["run_id", "cameras"]}}}]

    def names(self) -> set[str]:
        return {RENDER_TOOL}

    # -------------------------------------------------------------------------------------------- execucao
    def _check(self, call: ToolCall) -> dict:
        """Argumentos -> {build_run, fixture_ref, skp, skp_sha, spec, cameras} ou {'error': ...}. Nada abre aqui."""
        b = self.build
        args = dict(call.arguments)
        extra = sorted(set(args) - {"run_id", "cameras"})
        if extra:
            return _error("ARGUMENT_NOT_ALLOWED", f"argumentos nao publicados: {extra} (so run_id e cameras)")
        run_id = args.get("run_id")
        row = b.registry.by_run_id(run_id) if isinstance(run_id, str) and _RUN_ID.match(run_id) else None
        prior = json.loads(row["result"] or "{}") if row else {}
        ref = row["fixture_ref"] if row else None
        spec = self._spec(ref) if ref else None
        if row is None or row["status"] != "completed" or prior.get("tool", TOOL_NAME) != TOOL_NAME or spec is None:
            return _error("RUN_NOT_RENDERABLE", "run_id nao e um build completed deste executor com render_spec "
                                                f"(renderizaveis: {self.renderable_fixtures()})")
        skp = Path(str(prior.get("skp_abs") or "")).resolve()
        if not _under(skp, (b.out_root / run_id,)) or not skp.is_file():
            return _error("RUN_NOT_RENDERABLE", "o .skp do build nao esta mais na pasta do run")
        cams = args.get("cameras")
        allowed = self._cameras(spec)
        if (not isinstance(cams, list) or not 1 <= len(cams) <= MAX_CAMERAS or len(set(map(str, cams))) != len(cams)
                or not all(isinstance(c, str) and c in allowed for c in cams)):
            return _error("CAMERA_NOT_ALLOWED", f"1 a {MAX_CAMERAS} camaras distintas de {allowed}")
        if sha256_file(skp) != prior.get("skp_sha256"):
            return _error("SKP_CHANGED", "o .skp do build mudou desde a inspecao: nao renderizo outro arquivo")
        return {"build_run": run_id, "fixture_ref": ref, "skp": skp, "skp_sha": prior["skp_sha256"], "spec": spec,
                "cameras": list(cams)}

    def run(self, call: ToolCall, ctx: ToolContext) -> dict:
        b = self.build
        rid = b.request_id(call, ctx)
        prior = b.registry.get(rid)
        if prior is not None:
            return b._replay(prior)
        chk = self._check(call)
        if "error" in chk:
            return self._refuse(call, ctx, rid, chk)
        slot = self.slot()
        if not slot:
            return self._refuse(call, ctx, rid, _error("SLOT_NOT_GRANTED", "SketchUp/V-Ray sao exclusivos: o render "
                                                       "so roda com o slot concedido pelo coordenador"), **_ids(chk))
        ok, why = self.preflight()
        if not ok:
            return self._refuse(call, ctx, rid, _error("ENGINE_UNAVAILABLE", why), **_ids(chk))
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(b.clock()))
        run_id = f"lxr-{stamp}-{rid[3:15]}-{chk['fixture_ref']}"
        run_dir = b.out_root / run_id
        head, studio = b.head(b.engine_root), b.studio_head() or {"commit": "desconhecido", "dirty": None}
        tool_call = {
            "toolCallId": call.call_id, "kind": "execute", "status": "pending",
            "title": (f"Executor local (amostra) · {ctx.model_id}: renderizar {chk['build_run']} "
                      f"({', '.join(chk['cameras'])}) no V-Ray -> {run_dir}"),
            "rawInput": {
                "tool": RENDER_TOOL, "build_run_id": chk["build_run"], "cameras": chk["cameras"],
                "expected_skp_sha256": chk["skp_sha"], "slot": slot,
                "impacto": "abre SketchUp + V-Ray (GPU); cria so a pasta do render; nao toca o build nem artifacts/",
                "model": ctx.model_id, "model_digest": ctx.model_digest, "request_id": rid, "run_id": run_id,
                "render_spec": str(chk["spec"].relative_to(b.engine_root).as_posix()),
                "workspace_id": str(b.engine_root), "studio_commit": studio.get("commit"),
                "call_digest": call.digest, "model_tool_call": dict(call.raw)},
            "locations": [{"path": str(run_dir)}],
        }
        ctx.emit("tool_call", tool_call)
        choice = b._ask(ctx, tool_call)
        if choice != "allow_once":
            err = (_error("CANCELLED", "turno cancelado enquanto esperava a permissao") if ctx.cancel.is_set() else
                   _error("PERMISSION_DENIED", "recusado" if choice else "permissao fechada, vencida ou indisponivel"))
            return self._refuse(call, ctx, rid, err, **_ids(chk), run_id=run_id)
        if sha256_file(chk["skp"]) != chk["skp_sha"]:
            return self._refuse(call, ctx, rid, _error("SKP_CHANGED", "o .skp mudou depois da permissao"),
                                **_ids(chk), run_id=run_id)
        if not _ENGINE_LOCK.acquire(blocking=False):
            return self._refuse(call, ctx, rid, _error("BUSY", "ja ha uma execucao do motor em curso"), **_ids(chk))
        try:
            for other in b.registry.in_flight():
                b._replay(other)
            if b.registry.in_flight():
                return self._refuse(call, ctx, rid, _error("BUSY", "ha um pedido anterior sem resultado"), **_ids(chk))
            if b.sketchup_busy():
                return self._refuse(call, ctx, rid, _error("SKETCHUP_BUSY", "outro SketchUp esta aberto (slot "
                                                           "compartilhado)"), **_ids(chk))
            if run_dir.exists():
                return self._refuse(call, ctx, rid, _error("RUN_EXISTS", f"{run_id} ja existe"), **_ids(chk))
            return self._execute(call, ctx, rid, chk, run_id, slot, head, studio)
        finally:
            _ENGINE_LOCK.release()

    def _execute(self, call: ToolCall, ctx: ToolContext, rid: str, chk: dict, run_id: str, slot: str,
                 head: dict | None, studio: dict) -> dict:
        b = self.build
        prov = provenance(ctx, head, studio)
        prov["parent_run_id"] = chk["build_run"]
        engine = prov["roles_expected"]["builder"]["id"]
        prov["roles_expected"] = {"renderer": {"kind": "engine_vray", "id": engine,
                                               "note": "V-Ray pelo motor, camaras e cena da render_spec"}}
        base = {"tool": RENDER_TOOL, "request_id": rid, "run_id": run_id, "build_run_id": chk["build_run"],
                "fixture_ref": chk["fixture_ref"], "cameras": chk["cameras"], "slot": slot,
                "input_skp_sha256": chk["skp_sha"], "engine_commit": (head or {}).get("commit"),
                "workspace_id": str(b.engine_root), "mode": b.mode, "studio_commit": studio.get("commit"),
                "model": ctx.model_id, "model_digest": ctx.model_digest, "provider_version": ctx.provider_version,
                "session_id": ctx.session_id, "turn": ctx.turn, "call_digest": call.digest, "provenance": prov}
        b.registry.insert({**base, "fixture_ref": f"render:{chk['build_run']}", "fixture_hash": chk["skp_sha"],
                           "status": "running", "result": {**base, "status": "running"}}, b.clock())
        ctx.emit("tool_update", {"toolCallId": call.call_id, "status": "in_progress",
                                 "content": _text(f"permitido uma vez · request_id {rid} · render {run_id}\n"
                                                  "V-Ray em execucao pelo motor...")})
        requested_by = {"provider": ctx.provider, "model_id": ctx.model_id, "model_digest": ctx.model_digest}
        argv = [str(b.python), "-m", "p2s_engine.render", str(chk["spec"]), str(chk["skp"]),
                "--out-dir", str(b.out_root), "--run-id", run_id, "--cameras", *chk["cameras"], "--slot", slot,
                "--request-id", rid, "--parent-run-id", chk["build_run"],
                "--requested-by-json", json.dumps(requested_by, sort_keys=True)]
        log = b.out_root / "_studio" / f"{rid}.engine.log"
        started = b.clock()
        ident: dict[str, Any] = {}

        def on_start(pid: int, created: float | None) -> None:
            ident.update(engine_pid=pid, engine_created=created)
            b.registry.update(rid, "running", {**base, "status": "running", **ident}, b.clock())
        try:
            exit_ = b.runner(argv, b.engine_root, filtered_env(b.base_env), b.engine_timeout_s, log, on_start=on_start)
        except (OSError, subprocess.SubprocessError) as exc:
            result = {**base, "status": "failed", "engine_log": str(log), "started_at": started,
                      "finished_at": b.clock(), "error": {"code": "ENGINE_LAUNCH_FAILED",
                                                          "message": f"{type(exc).__name__}: {str(exc)[:200]}"}}
            b._finish(call, ctx, rid, result)
            return result
        result = {**base, **ident, "engine_pid": exit_.pid, "engine_returncode": exit_.returncode,
                  "engine_log": str(log), "started_at": started, "finished_at": b.clock()}
        if exit_.returncode is None:
            result.update(status="unknown", error={"code": "ENGINE_TIMEOUT", "message":
                          "o render nao terminou no prazo; reconciliado pelo run_id (sem novo render)"})
        else:
            result.update(read_render(b.out_root / run_id, chk["skp_sha"]))
        observe_renderer(prov, b.out_root / run_id)
        b._finish(call, ctx, rid, result)
        return result

    def _read_for_replay(self, run_dir: Path, prior: dict) -> dict:
        return read_render(run_dir, prior["fixture_hash"])

    def _refuse(self, call: ToolCall, ctx: ToolContext, rid: str, err: dict, **extra: Any) -> dict:
        res = self.build._refuse(call, ctx, rid, err, **extra)
        res["tool"] = RENDER_TOOL
        return res

    # -------------------------------------------------------------------------------------------- texto
    @staticmethod
    def model_text(result: dict) -> str:
        st = result.get("status")
        head = {"completed": "SUCESSO: o render V-Ray terminou; as imagens foram gravadas e conferidas.",
                "unknown": "RESULTADO AINDA DESCONHECIDO: o render nao terminou; nao ha imagem confirmada.",
                "refused": "NAO EXECUTADO: o pedido de render foi recusado antes de abrir o SketchUp."}.get(
            str(st), "FALHOU: o render nao ficou pronto.")
        lines = [head]
        for k in ("run_id", "build_run_id"):
            if result.get(k):
                lines.append(f"{k}: {result[k]}")
        for img in result.get("images") or []:
            lines.append(f"imagem: {img['png_abs']} (sha256 {img['sha256']})")
        if result.get("images"):
            lines.append("veredito visual: pendente (humano)")
        if result.get("error"):
            lines.append(f"erro: {result['error'].get('code')} - {result['error'].get('message')}")
        return "\n".join(lines)


def _ids(chk: dict) -> dict:
    return {"build_run_id": chk["build_run"], "fixture_ref": chk["fixture_ref"]}


def read_render(run_dir: Path, skp_sha: str) -> dict:
    """Resultado SO pelo render_manifest do motor e pelos PNG no disco."""
    mpath = run_dir / "render_manifest.json"
    try:
        man = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "failed", "error": {"code": "NO_MANIFEST", "message": "o motor nao gravou render_manifest.json"}}
    out: dict[str, Any] = {"run_dir": str(run_dir), "manifest_path": str(mpath), "render_verdict": man.get("verdict"),
                           "engine_status": man.get("status"), "visual_verdict": "PENDING_HUMAN"}
    if man.get("status") == "BLOCKED":
        return {**out, "status": "failed", "error": {"code": "RENDER_BLOCKED", "message": str(man.get("error"))[:300]}}
    if man.get("status") != "OK":
        return {**out, "status": "failed", "error": {"code": "RENDER_FAILED", "message":
                f"motor terminou {man.get('status')}: {str(man.get('error'))[:300]}"}}
    if (man.get("input_skp") or {}).get("sha256") != skp_sha:
        return {**out, "status": "failed", "error": {"code": "SKP_HASH_MISMATCH", "message":
                "o motor renderizou outro .skp"}}
    images = []
    for o in man.get("outputs") or []:
        png = (run_dir / str(o.get("png") or "")).resolve()
        if not o.get("png") or not png.is_relative_to(run_dir.resolve()) or not png.is_file() or png.stat().st_size == 0:
            return {**out, "images": images, "status": "failed", "error": {"code": "IMAGE_MISSING", "message":
                    f"imagem ausente ou vazia: {o.get('png')}"}}
        digest = sha256_file(png)
        if digest != o.get("png_sha256"):
            return {**out, "images": images, "status": "failed", "error": {"code": "IMAGE_HASH_MISMATCH", "message":
                    f"{o.get('png')}: o arquivo no disco nao e o que o motor registrou"}}
        images.append({"key": o.get("key"), "png_abs": str(png), "sha256": digest, "bytes": png.stat().st_size,
                       "kind": "vray_render"})
    out["images"] = images
    if not images:
        return {**out, "status": "failed", "error": {"code": "IMAGE_MISSING", "message": "nenhum quadro gravado"}}
    if man.get("verdict") != "PASS":
        return {**out, "status": "failed", "error": {"code": "RENDER_FAILED", "message":
                f"veredito do render: {man.get('verdict')}"}}
    return {**out, "status": "completed", "error": None}


def observe_renderer(prov: dict, run_dir: Path) -> dict:
    """`renderer` so quando o motor produziu ao menos um quadro (render_manifest OK com outputs)."""
    try:
        man = json.loads((run_dir / "render_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return prov
    exp = (prov.get("roles_expected") or {}).get("renderer")
    if exp and man.get("status") == "OK" and man.get("outputs"):
        commit = (man.get("engine") or {}).get("commit")
        prov["roles"]["renderer"] = {**exp, **({"id": f"sketchup-mcp-next@{commit}"} if commit else {}),
                                     "verdict": man.get("verdict")}
    return prov


class ToolBox:
    """Varias ferramentas do executor sob UM tool_host: publica todas e despacha pelo nome da chamada."""

    def __init__(self, tools: list):
        self.tools = list(tools)
        self._by_name = {n: t for t in self.tools for n in t.names()}

    def specs(self) -> list[dict]:
        return [s for t in self.tools for s in t.specs()]

    def names(self) -> set[str]:
        return set(self._by_name)

    def preflight(self) -> tuple[bool, str]:
        return self.tools[0].preflight()

    def run(self, call: ToolCall, ctx: ToolContext) -> dict:
        return self._by_name[call.name].run(call, ctx)

    def system_prompt(self) -> str:
        return ("Voce e o Executor local (amostra) do P2S Studio. Ferramentas: build_room_sample (constroi uma amostra "
                "de comodo no SketchUp 2026 a partir de uma fixture permitida) e render_room_sample (renderiza com "
                "V-Ray um build seu ja concluido, pelo run_id, em 1 ou 2 camaras). O Studio executa de verdade, depois "
                "da permissao; o desfecho e escrito pelo Studio a partir do motor.")

    def _text(self, result: dict) -> str:
        tool = self._by_name.get(str(result.get("tool") or TOOL_NAME))
        return tool.model_text(result) if tool is not None and hasattr(tool, "model_text") else json.dumps(result)

    def model_view(self, result: dict) -> str:
        return self._text(result)

    def final_text(self, results: list[dict]) -> str:
        parts = []
        for r in results:
            if r.get("request_id") or r.get("run_id"):
                parts.append(self._text(r))
            else:
                err = r.get("error") or {}
                parts.append(f"NAO EXECUTADO: {err.get('code')} - {err.get('message')}")
        return "Resultado do motor (escrito pelo Studio, nao pelo modelo):\n" + "\n\n".join(parts)
