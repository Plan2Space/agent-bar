"""store_reader.py — leitura SOMENTE LEITURA do EventStore para o painel (Agent Bar).

Portado parcialmente de `tools/orchestration/agent_bar/collectors.py` (sketchup-mcp, branch do PR #663, HEAD 8b15414b):
`collect_store`, `redact_secrets` e `_now`. A logica de leitura e a mesma; o que mudou:

- o caminho do banco e um parametro explicito. A resolucao por variaveis de ambiente (`store_db_path`, que depende do
  `store_factory` do sketchup-mcp) NAO foi portada: a configuracao do caminho ainda nao tem contrato no agent-runtime.
- a barra nao importa nada do runtime. Ela le o schema por contrato (tabelas `events`, `transitions`, `outbox`).

A barra nao e fonte autoritativa: quem escreve e o runtime. Nada aqui cria, altera ou trava o banco.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

_SECRET = re.compile(
    # cabecalho Authorization/Proxy-Authorization: o VALOR INTEIRO sai, qualquer que seja o esquema (Basic, Bearer, token, OAuth, Digest, AWS4-HMAC-SHA256...)
    # aspas opcionalmente ESCAPADAS em volta da chave (`{\"Authorization\": \"OAuth x\"}` num erro com JSON escapado); valor inteiro ate o fim da linha (`;` tambem)
    r"((?i:(?<![A-Za-z0-9_-])(?:proxy-)?authorization)(?:\\?[\"'])?\s*[:=]\s*(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\r\n]+)"
    r"|gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}|sk-[A-Za-z0-9][A-Za-z0-9_-]{15,}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{12,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}|(?i:basic)\s+[A-Za-z0-9+/=]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|(?i:(?<![A-Za-z0-9])[A-Za-z0-9_-]*(?:api[_-]?key|secret|password|passwd|token|credential|authorization)[A-Za-z0-9_-]*)[\"']?\s*[:=]\s*"
    r"(?:(?i:bearer|basic|token)\s+)?"                                    # `Authorization: Basic <b64>` e `token=Bearer abc`: o esquema nao encerra o valor                                    # `token=Bearer abc...`: o esquema nao encerra o valor, o valor opaco tambem sai
    r"(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\s;}\"']{1,}))")        # valor ENTRE ASPAS inteiro (virgulas, espacos, escapes) ou sem aspas ate o delimitador; inclui JSON


def redact_secrets(text: object) -> str:
    """Credenciais saem de todo texto que vem do banco ANTES de ir para o painel ou para o diagnostico copiado (a razao de `EventStore.fail` e livre)."""
    return _SECRET.sub("<segredo>", str(text or ""))


def _now(now: dt.datetime | None) -> dt.datetime:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)


def collect_store(db_path: Path, now: dt.datetime | None = None) -> dict:
    """O EventStore lido SOMENTE LEITURA (`mode=ro`, URI codificada). Banco ausente ou ilegivel devolve estado explicito
    (`ausente`/`indisponivel`), nunca um zero que pareca saudavel; nada e criado nem alterado."""
    import sqlite3
    path = Path(db_path)
    if not path.is_file():
        return {"state": "ausente", "error": f"{path.name} nao existe em {path.parent.name}"}
    ts = _now(now).timestamp()
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute(
                "SELECT id,event_type,idempotency_key,status,attempt,available_at,lease_owner,lease_until,error,updated_at FROM events ORDER BY id")]
            # a falha mais recente e a da ORDEM PERSISTIDA das transicoes (id crescente), nunca o relogio de parede (ele pode recuar). UMA passagem agrupada pela tabela
            # (MAX(id) por evento) + busca pela chave primaria: sem subconsulta correlacionada, que varreria `transitions` inteira para cada falha (sem indice por event_id)
            failed_at = {r["event_id"]: r["tid"] for r in db.execute(
                "SELECT event_id, MAX(id) AS tid FROM transitions WHERE from_status='RUNNING' AND to_status IN ('PENDING','DLQ') GROUP BY event_id")}
            # a OUTBOX persistente (publicacao no GitHub etc.) tem fila, lease, retries e DLQ proprios: um evento DONE cuja publicacao foi para a DLQ da outbox e PERDA real
            orows = [dict(r) for r in db.execute(
                "SELECT id,event_id,target,operation,idempotency_key,status,attempts,available_at,lease_until,error FROM outbox ORDER BY id")]
        finally:
            db.close()
    except sqlite3.Error as exc:
        return {"state": "indisponivel", "error": f"{type(exc).__name__}: {exc}"[:160]}
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    live = [{"key": redact_secrets(r["idempotency_key"]), "owner": redact_secrets(r["lease_owner"] or "?"), "seconds_left": int(r["lease_until"] - ts)}
            for r in rows if r["status"] == "RUNNING" and r["lease_until"] > ts]
    # o ultimo erro e o da FALHA mais recente (transicao RUNNING -> PENDING/DLQ), nao o do evento tocado por ultimo: claim, renovacao e conclusao tambem mexem em
    # `updated_at`. Evento ja concluido (DONE) nao e erro corrente.
    errored = sorted((r for r in rows if r["error"] and r["status"] not in ("DONE", "SUPERSEDED") and r["id"] in failed_at), key=lambda r: failed_at[r["id"]])
    ocounts: dict[str, int] = {}
    for r in orows:
        ocounts[r["status"]] = ocounts.get(r["status"], 0) + 1
    outbox = {"counts": ocounts, "zombies": sum(1 for r in orows if r["status"] == "RUNNING" and r["lease_until"] <= ts),
              "retries": sum(1 for r in orows if r["attempts"] > 1),
              "dlq": [{"id": r["id"], "event_id": r["event_id"], "target": redact_secrets(r["target"]), "key": redact_secrets(r["idempotency_key"]),
                       "error": redact_secrets(r["error"] or "")[:160],
                       "unknown_outcome": "remote outcome requires reconciliation" in (r["error"] or "")} for r in orows if r["status"] == "DLQ"]}
    return {"state": "ok", "outbox": outbox, "counts": counts, "backoff": sum(1 for r in rows if r["status"] == "PENDING" and r["available_at"] > ts),
            "live_leases": live, "zombies": sum(1 for r in rows if r["status"] == "RUNNING" and r["lease_until"] <= ts),
            "dlq": [{"id": r["id"], "key": redact_secrets(r["idempotency_key"]), "error": redact_secrets(r["error"])[:160]} for r in rows if r["status"] == "DLQ"],
            "retries": sum(1 for r in rows if r["attempt"] > 1),
            "last_error": ({"key": redact_secrets(errored[-1]["idempotency_key"]), "error": redact_secrets(errored[-1]["error"])} if errored else None)}
