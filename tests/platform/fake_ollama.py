"""fake_ollama.py — provedor HTTP local falso (harness H3): um servidor HTTP de verdade numa thread, com as rotas do
Ollama que o adaptador usa (/api/version, /api/tags, /api/show, /api/chat em NDJSON).

Modelos: `chat-a` (completion+tools), `think-b` (completion+thinking, values [false, true]) e `embed-c` (so
embedding, nao pode aparecer como modelo de conversa). O prompt com "devagar" gera devagar ate o cliente fechar.
Registra cada corpo recebido em `requests` e quantos streams foram interrompidos pelo cliente em `aborted`.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = {
    "chat-a": {"capabilities": ["completion", "tools"], "model_info": {"llama.context_length": 8192}},
    "think-b": {"capabilities": ["completion", "thinking"], "thinking": {"values": [False, True], "default": True},
                "model_info": {"qwen3.context_length": 40960}},
    "embed-c": {"capabilities": ["embedding"], "model_info": {"bert.context_length": 2048}},
    "vision-d": {"capabilities": ["completion", "vision"], "model_info": {"qwen25vl.context_length": 32768}},
}


def fake_digest(name: str) -> str:
    """Digest determinístico como o do /api/tags (64 hex)."""
    import hashlib
    return hashlib.sha256(f"fake-ollama/{name}".encode()).hexdigest()


IMAGE_TOKENS = 256                                                         # como o Ollama: a imagem entra no prompt


class FakeOllama:
    def __init__(self, models: dict | None = None):
        self.models = MODELS if models is None else models
        self.requests: list[tuple[str, dict]] = []
        self.aborted = 0
        self.no_digest = False                                                # /api/tags sem digest
        self.no_counts = False                                                # nenhum turno manda os contadores
        self.image_reply: str | None = None                                  # resposta fixa dos turnos com imagem
        # tool calling (P2S-LOCAL-EXECUTOR): o que o "modelo" devolve quando o pedido traz `tools`:
        # {"calls": [...raw tool_calls...], "content": "texto", "drop": True (corta antes do done),
        #  "again": True (devolve tool_calls tambem na rodada com o resultado)}
        self.tool_script: dict | None = None
        self.digests: dict[str, str] = {}                                   # digest substituto (modelo re-baixado)
        self.reply_model: str | None = None                                 # o servidor diz ter usado OUTRO modelo
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):                                       # silencioso nos testes
                pass

            def _json(self, data, code=200):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/api/version":
                    return self._json({"version": "0.40.16-fake"})
                if self.path == "/api/tags":
                    tags = [{"name": n, "model": n, **({} if outer.no_digest else {"digest": outer.digests.get(n) or fake_digest(n)})}
                            for n in outer.models]
                    return self._json({"models": tags})
                return self._json({"error": "not found"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append((self.path, body))
                if self.path == "/api/show":
                    m = outer.models.get(body.get("model"))
                    return self._json(m) if m else self._json({"error": "model not found"}, 404)
                if self.path == "/api/chat":
                    return self._chat(body)
                return self._json({"error": "not found"}, 404)

            def _chat(self, body):
                model = body.get("model")
                if model in outer.models and outer.reply_model:
                    model_reply = outer.reply_model
                else:
                    model_reply = None
                if model not in outer.models:
                    return self._json({"error": f"model '{model}' not found"}, 404)
                if "think" in body and "thinking" not in outer.models[model]["capabilities"]:
                    return self._json({"error": f"{model} does not support thinking"}, 400)
                last = body["messages"][-1]["content"]
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()

                def line(obj):
                    self.wfile.write((json.dumps(obj) + "\n").encode())
                    self.wfile.flush()
                try:
                    if body.get("think") is True:
                        line({"model": model_reply or model, "message": {"role": "assistant", "thinking": "pensando..."}, "done": False})
                    script = outer.tool_script
                    role = body["messages"][-1].get("role")
                    if script is not None and role == "user" and ("tools" in body or script.get("untold")):
                        if script.get("content"):
                            line({"model": model_reply or model, "message": {"role": "assistant", "content": script["content"]},
                                  "done": False})
                        if script.get("calls"):
                            line({"model": model_reply or model, "message": {"role": "assistant", "content": "",
                                                              "tool_calls": script["calls"]}, "done": False})
                        if script.get("drop"):
                            return                                           # stream cortado: sem done
                    elif script is not None and role == "tool":
                        if script.get("again") and script.get("calls"):
                            line({"model": model_reply or model, "message": {"role": "assistant", "content": "",
                                                              "tool_calls": script["calls"]}, "done": False})
                        line({"model": model_reply or model, "message": {"role": "assistant",
                                                          "content": "resultado: " + str(last)[:60]}, "done": False})
                    elif "devagar" in last:
                        for i in range(2000):
                            line({"model": model_reply or model, "message": {"role": "assistant", "content": f"{i} "}, "done": False})
                            time.sleep(0.02)
                    elif body["messages"][-1].get("images"):            # modelo com vision recebeu imagem
                        if "vision" not in outer.models[model]["capabilities"]:
                            line({"error": "model does not support images"})
                            return
                        imgs = body["messages"][-1]["images"]
                        text = (outer.image_reply if outer.image_reply is not None
                                else f"imagens={len(imgs)} bytes={sum(len(i) for i in imgs)}")
                        line({"model": model_reply or model, "message": {"role": "assistant", "content": text}, "done": False})
                    elif "historico" in last:
                        n = sum(1 for m in body["messages"] if m["role"] == "user")
                        line({"model": model_reply or model, "message": {"role": "assistant", "content": f"users={n}"}, "done": False})
                    else:
                        for part in ("o", "k"):
                            line({"model": model_reply or model, "message": {"role": "assistant", "content": part}, "done": False})
                    final = {"model": model_reply or model, "message": {"role": "assistant", "content": ""}, "done": True,
                             "done_reason": "stop",
                             "prompt_eval_count": 13 + IMAGE_TOKENS * len(body["messages"][-1].get("images") or []),
                             "prompt_eval_cached_count": 0, "eval_count": 2}
                    if "semcontagem" in last or outer.no_counts:                         # servidor que nao manda os contadores
                        final = {k: v for k, v in final.items() if not k.endswith("_count")}
                    line(final)
                except (ConnectionError, OSError):
                    outer.aborted += 1                                       # o cliente fechou: geracao interrompida

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
