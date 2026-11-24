"""health.py — linhas de "Configurações → Saúde do sistema" (UX-06 do P2S-STUDIO-UX-CONSOLIDATION-F0). Sem Qt.

Só fatos observados, cada um com a sua origem. Regra do pedido do Felipe: conectividade não é capacidade de execução,
então "Claude conectado" (login/processo vistos pela coleta) e "Executor pronto" (sessão do Chat aberta) são DUAS
linhas; ferramentas MCP não são afirmadas aqui (a prova delas é do UX-12).
"""
from __future__ import annotations

import platform
from dataclasses import dataclass, field, replace

from agent_bar.store_reader import redact_secrets

OK, WARN, BAD, UNKNOWN = "ok", "atenção", "falha", "desconhecido"
NOT_EXECUTOR = "não é executor"                    # cinza: conectado, mas o agente não executa ferramentas
EXECUTOR_KEYS = frozenset({"claude"})              # perfis que executam ferramentas (Claude Code via ACP)


@dataclass(frozen=True)
class HealthRow:
    name: str
    state: str
    lines: list[str] = field(default_factory=list)


def _level_state(level) -> str:
    name = getattr(level, "name", str(level))
    return {"GREEN": OK, "BLUE": OK, "YELLOW": WARN, "RED": BAD}.get(name, UNKNOWN)


def app_row(checkout_sha: str) -> HealthRow:
    try:
        import PySide6
        qt = PySide6.__version__
    except Exception:                                                         # noqa: BLE001
        qt = "?"
    sha = checkout_sha[:7] if checkout_sha else "desconhecido (sem git)"
    return HealthRow("P2S Studio", OK if checkout_sha else UNKNOWN,
                     [f"Python {platform.python_version()} · PySide6 {qt} · checkout {sha}"])


def runtime_row(pin) -> HealthRow:
    if pin is None:
        return HealthRow("agent-runtime", UNKNOWN, ["conferência não feita"])
    state = OK if pin.ok is True else BAD if pin.ok is False else UNKNOWN
    return HealthRow("agent-runtime", state, [pin.message])


def claude_rows(snap, chat) -> list[HealthRow]:
    if snap is None:
        conn = HealthRow("Claude conectado", UNKNOWN, ["a coleta ainda não chegou"])
    else:
        c = snap.claude
        conn = HealthRow("Claude conectado", _level_state(c.level), [c.line()])
    return [conn, executor_row(chat)]


def executor_row(chat) -> HealthRow:
    """OK só quando a sessão é de um agente que executa ferramentas (Claude ACP), está pronta e já mostrou
    ferramentas nesta sessão. Agente de teste, demonstração ou Ollama: "não é executor". MCP não conferido nunca conta."""
    name = "Executor pronto"
    if chat is None:
        return HealthRow(name, UNKNOWN, ["Cockpit sem Chat"])
    if getattr(chat, "client", None) is None:
        return HealthRow(name, WARN, ["não: nenhuma sessão do Chat aberta"])
    status = chat.status.text() if hasattr(chat, "status") else "sessão aberta"
    prof = chat.profile() if callable(getattr(chat, "profile", None)) else None
    label = getattr(prof, "label", "agente desconhecido")
    executor = (getattr(prof, "key", "") in EXECUTOR_KEYS and getattr(prof, "adapter", "") == "acp"
                and not getattr(prof, "demo", False))
    if not executor:
        return HealthRow(name, NOT_EXECUTOR, [f"não: {label} não é executor (sem ferramentas)", f"sessão do Chat: {status}"])
    mcp = "ferramentas MCP: não conferidas aqui"
    if "conectado" not in status and "pronto" not in status:
        return HealthRow(name, WARN, [f"não: sessão do Chat {status}", mcp])
    tools = sorted(str(t) for t in getattr(chat, "tools_observed", ()) or ())
    if not tools:
        return HealthRow(name, WARN, [f"sessão do Chat: {status}",
                                      "nenhuma ferramenta usada nesta sessão ainda (o ACP não lista ferramentas ao abrir)",
                                      mcp])
    shown = ", ".join(tools[:6]) + (" …" if len(tools) > 6 else "")
    return HealthRow(name, OK, [f"sessão do Chat: {status}", f"ferramentas usadas nesta sessão: {len(tools)} ({shown})", mcp])


def permissions_row() -> HealthRow:
    """Em que modo o Studio responde aos pedidos de permissão dos agentes (bypass é a decisão do Felipe)."""
    from agent_bar.cockpit import permission_mode as PM
    mode = PM.current()
    return HealthRow("Permissões dos agentes", OK, [PM.label(mode), "cada aprovação automática fica registrada (Inbox, "
                                                   "Notificações); operação irreversível continua pedindo o código"
                                                   if mode == PM.BYPASS else "cada ferramenta pede confirmação"])


def github_row(snap) -> HealthRow:
    if snap is None:
        return HealthRow("GitHub", UNKNOWN, ["a coleta ainda não chegou"])
    g, q = snap.github, snap.pr_queue
    queue = (f"fila de PRs: leitura falhou ({q.error})" if q.error and not q.rows
             else f"fila de PRs: {len(q.rows)} aberto(s), lida {q.synced or 'sem hora'}"
             + (f" (última leitura com erro: {q.error})" if q.error else ""))
    state = _level_state(g.level)
    if q.error and state == OK:
        state = WARN
    return HealthRow("GitHub", state, [g.line(), queue])


def eventstore_row(snap) -> HealthRow:
    if snap is None:
        return HealthRow("EventStore", UNKNOWN, ["a coleta ainda não chegou"])
    o = snap.observation
    if o.source != "lido":
        return HealthRow("EventStore", WARN if o.source == "demonstração" else UNKNOWN, [f"{o.source}: {o.reason}"])
    counts = " · ".join(f"{k} {v}" for k, v in sorted(o.counts.items())) or "sem contagens"
    age = f"última transição há {o.last_event_age_s} s" if o.last_event_age_s is not None else "sem transições"
    return HealthRow("EventStore", OK, [f"{o.db_path} · schema {o.schema_version or '?'}", f"{counts} · {age}"])


def sources_row(review) -> HealthRow:
    text = review.sources_text() if review is not None and hasattr(review, "sources_text") else ""
    states = getattr(review, "data_states", {}) if review is not None else {}
    if not text:
        return HealthRow("Fontes da Revisão visual", UNKNOWN, ["ainda não lidas (abra a Revisão visual)"])
    bad = any(getattr(s, "status", "") != "AO_VIVO" for s in states.values())
    return HealthRow("Fontes da Revisão visual", WARN if bad else OK, text.split("  │  "))


def health_rows(core, chat=None, review=None, checkout_sha: str = "") -> list[HealthRow]:
    snap = getattr(core, "snap", None) if core is not None else None
    pin = getattr(core, "runtime_pin", None) if core is not None else None
    rows = [runtime_row(pin), app_row(checkout_sha), *claude_rows(snap, chat), permissions_row(), github_row(snap),
            eventstore_row(snap), sources_row(review)]
    return [replace(r, lines=[redact_secrets(line) for line in r.lines]) for r in rows]   # #106: nada de segredo na tela


def diagnostic_text(core, rows: list[HealthRow]) -> str:
    """Texto do "Copiar diagnóstico": as linhas de saúde e o diagnóstico da coleta. Sem segredos (só estado observado)."""
    out = ["P2S Studio — diagnóstico", ""]
    for r in rows:
        out.append(f"[{r.state}] {r.name}")
        out += [f"    {line}" for line in r.lines]
    snap = getattr(core, "snap", None) if core is not None else None
    out += ["", snap.diagnostic() if snap is not None else "coleta: ainda não chegou"]
    return redact_secrets("\n".join(out))                                       # #106: nem no texto copiado


__all__ = ["BAD", "EXECUTOR_KEYS", "NOT_EXECUTOR", "OK", "UNKNOWN", "WARN", "HealthRow", "app_row", "claude_rows",
           "diagnostic_text", "eventstore_row", "executor_row",
           "github_row", "health_rows", "runtime_row", "sources_row"]
