"""usage.py — telemetria de tokens e contexto com estado por campo (SPEC P2S-STUDIO-WORKSPACE-UX, F6).

Cada valor diz de onde veio e quanto vale como prova:
- MEASURED: o provedor informou o numero (ex.: `eval_count` do Ollama, `usage_update.used` do ACP);
- ESTIMATED: calculado aqui ou pelo provedor sem ser medicao do que foi cobrado/usado (ex.: custo equivalente de API
  numa assinatura; contexto do Ollama somado dos contadores do turno);
- UNAVAILABLE: o provedor nao informa. Nunca vira zero;
- STALE: era MEASURED/ESTIMATED, mas deixou de valer (troca de modelo, ou a atualizacao seguinte nao trouxe o campo).

Regras: estimativa nunca aparece como medicao; o maximo do modelo (`/api/show`) nao e a janela da sessao; custo
equivalente de API nao e fatura. Limite da conta: o claude-agent-acp INFORMA a utilizacao das janelas de 5 h e 7 dias
(`usage_update._meta["_claude/rateLimit"]`, prova real de 2026-11-23) — MEASURED com o horario de renovacao; o Ollama
nao tem. (Os PRs #84/#85 diziam UNAVAILABLE para o limite da conta: estava errado, corrigido aqui.)
Totais da sessao do ACP sao do MODELO PRINCIPAL; modelos de fundo (ex.: haiku) aparecem separados, sem somar.
O que cada provedor informa de fato esta medido em agent-bar (F6, achados de 2026-11-23).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from typing import Any

STATES = ("MEASURED", "ESTIMATED", "UNAVAILABLE", "STALE")
METRICS = ("input_tokens", "output_tokens", "context_used", "context_window", "model_max_context",
           "cost_api_equivalent_usd", "session_input_tokens", "session_output_tokens", "session_cache_tokens",
           "session_background_tokens", "account_5h_utilization", "account_7d_utilization")
ACCOUNT_WINDOWS = {"five_hour": "account_5h_utilization", "seven_day": "account_7d_utilization"}
SCHEMA = "usage-snapshot.v1"


@dataclass(frozen=True)
class Metric:
    value: float | int | None
    state: str
    source: str = ""
    resets_at: float | None = None                                           # epoch UTC (janelas de limite da conta)

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"estado desconhecido: {self.state}")
        if self.state == "UNAVAILABLE" and self.value is not None:
            raise ValueError("UNAVAILABLE nao tem valor (nunca vira zero)")
        if self.state in ("MEASURED", "ESTIMATED", "STALE") and self.value is None:
            raise ValueError(f"{self.state} precisa de valor")

    @property
    def known(self) -> bool:
        return self.state != "UNAVAILABLE"


def unavailable(source: str) -> Metric:
    return Metric(None, "UNAVAILABLE", source)


def _num(v: Any) -> int | float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


@dataclass(frozen=True)
class UsageSnapshot:
    provider_id: str
    model_id: str
    input_tokens: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    output_tokens: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    context_used: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    context_window: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    model_max_context: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    cost_api_equivalent_usd: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    session_input_tokens: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    session_output_tokens: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    session_cache_tokens: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    session_background_tokens: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    account_5h_utilization: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    account_7d_utilization: Metric = field(default_factory=lambda: unavailable("nao informado pelo provedor"))
    model_reported: str = ""                                                 # o modelo que o provedor diz ter usado
    stale_reason: str = ""
    covers: tuple[str, ...] = METRICS                                        # campos que ESTA fonte informa

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"schema": SCHEMA, "provider_id": self.provider_id, "model_id": self.model_id,
                               "stale_reason": self.stale_reason, "covers": list(self.covers),
                               "model_reported": self.model_reported}
        for name in METRICS:
            m: Metric = getattr(self, name)
            out[name] = {"value": m.value, "state": m.state, "source": m.source, "resets_at": m.resets_at}
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> UsageSnapshot:
        if d.get("schema") != SCHEMA:
            raise ValueError(f"schema inesperado: {d.get('schema')!r}")
        metrics = {n: Metric(d[n]["value"], d[n]["state"], d[n].get("source", ""), d[n].get("resets_at"))
                   for n in METRICS if n in d}
        covers = tuple(c for c in d.get("covers", METRICS) if c in METRICS)  # campo novo ausente = nao informado
        return cls(str(d.get("provider_id", "")), str(d.get("model_id", "")), stale_reason=str(d.get("stale_reason", "")),
                   covers=covers, model_reported=str(d.get("model_reported", "")), **metrics)

    def stale(self, why: str) -> UsageSnapshot:
        """Tudo que era conhecido vira STALE (ex.: troca de modelo); o que era UNAVAILABLE continua."""
        changes = {n: replace(getattr(self, n), state="STALE") for n in METRICS if getattr(self, n).known}
        return replace(self, stale_reason=why, **changes)

    def merged_with(self, newer: UsageSnapshot) -> UsageSnapshot:
        """A atualizacao seguinte vence nos campos que a fonte dela cobre; campo coberto que ela nao traz mantem o valor
        anterior, marcado STALE; campo que a fonte nao cobre (ex.: a resposta do prompt nao fala de contexto) fica
        como estava."""
        if (newer.provider_id, newer.model_id) != (self.provider_id, self.model_id):
            return newer
        changes = {}
        for n in METRICS:
            old, new = getattr(self, n), getattr(newer, n)
            if n not in newer.covers:
                changes[n] = old
            elif not new.known and old.known:
                changes[n] = replace(old, state="STALE")
        return replace(newer, covers=tuple(dict.fromkeys((*self.covers, *newer.covers))),
                       model_reported=newer.model_reported or self.model_reported, **changes)


# ------------------------------------------------------------------------------------------------ provedores
def from_ollama_done(msg: Mapping[str, Any], *, provider_id: str, model_id: str, model_max_context: int | None,
                     num_ctx: int | None = None) -> UsageSnapshot:
    """Ultimo chunk (`done: true`) do `/api/chat`. Contadores do turno sao MEASURED; o contexto ocupado e ESTIMATED
    (o Ollama nao informa e pode truncar em `num_ctx`); a janela so e conhecida se o Studio fixou `num_ctx`."""
    pin, pcached, pout = _num(msg.get("prompt_eval_count")), _num(msg.get("prompt_eval_cached_count")), _num(msg.get("eval_count"))
    used = (pin or 0) + (pcached or 0) + (pout or 0) if pin is not None and pout is not None else None
    return UsageSnapshot(
        provider_id, model_id,
        input_tokens=Metric(pin, "MEASURED", "ollama prompt_eval_count") if pin is not None else unavailable("ollama: sem prompt_eval_count"),
        output_tokens=Metric(pout, "MEASURED", "ollama eval_count") if pout is not None else unavailable("ollama: sem eval_count"),
        context_used=(Metric(used, "ESTIMATED", "soma prompt_eval_count + prompt_eval_cached_count + eval_count")
                      if used is not None else unavailable("ollama nao informa o contexto ocupado")),
        context_window=(Metric(num_ctx, "MEASURED", "options.num_ctx fixado pelo Studio") if num_ctx
                        else unavailable("ollama nao informa o num_ctx em uso")),
        model_max_context=(Metric(model_max_context, "MEASURED", "/api/show model_info *.context_length")
                           if model_max_context else unavailable("/api/show sem context_length")),
        cost_api_equivalent_usd=unavailable("provedor local: sem custo de API"),
    )


def from_acp_usage_update(data: Mapping[str, Any], *, provider_id: str, model_id: str) -> UsageSnapshot:
    """`session/update` `usage_update` do ACP: `used`/`size` MEASURED (informados pelo agente); `cost` em USD e
    ESTIMATED: e o preco equivalente de API, nao o que a assinatura cobra."""
    used, size = _num(data.get("used")), _num(data.get("size"))
    cost = data.get("cost") if isinstance(data.get("cost"), Mapping) else {}
    amount = _num(cost.get("amount")) if str(cost.get("currency", "")).upper() == "USD" else None
    meta = data.get("_meta") if isinstance(data.get("_meta"), Mapping) else {}
    covers = ["context_used", "context_window", "cost_api_equivalent_usd"]
    account: dict[str, Metric] = {}
    rate = meta.get("_claude/rateLimit")
    windows = rate.get("unifiedWindows") if isinstance(rate, Mapping) else None
    if isinstance(windows, Mapping):                                         # so cobre o limite quando o update o traz
        for key, name in ACCOUNT_WINDOWS.items():
            w = windows.get(key) if isinstance(windows.get(key), Mapping) else {}
            util = _num(w.get("utilization"))
            account[name] = (Metric(util, "MEASURED", f"acp _claude/rateLimit {key}.utilization",
                                    _num(w.get("resetsAt"))) if util is not None
                             else unavailable(f"acp: janela {key} sem utilization"))
            covers.append(name)
    model = meta.get("_claude/model")
    return UsageSnapshot(
        provider_id, model_id,
        context_used=Metric(used, "MEASURED", "acp usage_update.used") if used is not None else unavailable("acp: sem used"),
        context_window=Metric(size, "MEASURED", "acp usage_update.size") if size else unavailable("acp: sem size"),
        cost_api_equivalent_usd=(Metric(amount, "ESTIMATED", "acp usage_update.cost (preco equivalente de API)")
                                 if amount is not None else unavailable("acp: custo nao informado neste update")),
        model_reported=str(model) if isinstance(model, str) else "",
        covers=tuple(covers), **account,
    )


def from_acp_prompt_usage(usage: Mapping[str, Any], *, provider_id: str, model_id: str) -> UsageSnapshot:
    """`usage` da resposta do `session/prompt` (claude-agent-acp): contadores ACUMULADOS da sessao, nao do turno, do
    MODELO PRINCIPAL; cache = leitura + escrita. `usage["_meta"]` (o `_meta` da resposta) traz `quota.model_usage` por
    modelo: o principal e o que bate com `totalTokens`; os demais (fundo) somam em `session_background_tokens`, a parte."""
    inp, out = _num(usage.get("inputTokens")), _num(usage.get("outputTokens"))
    rd, wr = _num(usage.get("cachedReadTokens")), _num(usage.get("cachedWriteTokens"))
    cache = (rd or 0) + (wr or 0) if rd is not None or wr is not None else None
    src = "acp resposta do prompt: usage acumulado da sessao (modelo principal)"
    meta = usage.get("_meta") if isinstance(usage.get("_meta"), Mapping) else {}
    quota = meta.get("quota") if isinstance(meta.get("quota"), Mapping) else {}
    per_model = [m for m in quota.get("model_usage") or [] if isinstance(m, Mapping)]
    total = _num(usage.get("totalTokens"))
    main = next((m for m in per_model if total is not None
                 and _num((m.get("token_count") or {}).get("totalTokens")) == total), None)
    if not per_model:
        background = unavailable("acp: sem _meta.quota.model_usage")
    elif main is None:
        background = unavailable("acp: nao foi possivel separar o modelo principal em model_usage")
    else:
        others = [m for m in per_model if m is not main]
        counts = [_num((m.get("token_count") or {}).get("totalTokens")) for m in others]
        names = ", ".join(str(m.get("model", "?")) for m in others) or "nenhum"
        background = (Metric(sum(counts), "MEASURED", f"acp _meta.quota.model_usage, modelos de fundo: {names}")
                      if all(c is not None for c in counts) else unavailable("acp: contagem de fundo incompleta"))
    return UsageSnapshot(
        provider_id, model_id,
        session_input_tokens=Metric(inp, "MEASURED", src) if inp is not None else unavailable("acp: sem inputTokens"),
        session_output_tokens=Metric(out, "MEASURED", src) if out is not None else unavailable("acp: sem outputTokens"),
        session_cache_tokens=Metric(cache, "MEASURED", src) if cache is not None else unavailable("acp: sem cache"),
        session_background_tokens=background,
        model_reported=str(main.get("model", "")) if main is not None else "",
        covers=("session_input_tokens", "session_output_tokens", "session_cache_tokens", "session_background_tokens"),
    )


# ------------------------------------------------------------------------------------------------ exibicao
STATE_WORD = {"MEASURED": "medido", "ESTIMATED": "estimado", "STALE": "desatualizado"}


def _tokens(v: float) -> str:
    v = int(v)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if v >= 10_000:
        return f"{round(v / 1000)}k"
    return f"{v / 1000:.1f}k".replace(".0k", "k") if v >= 1000 else str(v)


def _resets(ts: float | None, tz: tzinfo | None, with_date: bool) -> str:
    if ts is None:
        return ""
    when = datetime.fromtimestamp(ts, tz or timezone.utc) if tz else datetime.fromtimestamp(ts).astimezone()
    return f", renova {when.strftime('%d/%m %H:%M' if with_date else '%H:%M')}"


def usage_line(s: UsageSnapshot | None, tz: tzinfo | None = None) -> str:
    """Uma linha para o Chat. Diz o estado de cada numero; o que nao foi informado aparece como tal.
    `tz`: fuso para o horario de renovacao (padrao: o da maquina)."""
    if s is None:
        return "uso: não informado pelo provedor"
    parts = []
    if s.model_reported and s.model_reported != s.model_id:
        parts.append(f"modelo {s.model_reported}")
    used, win = s.context_used, s.context_window
    if used.known and win.known:
        word = STATE_WORD[used.state] if used.state == win.state else f"{STATE_WORD[used.state]}/{STATE_WORD[win.state]}"
        parts.append(f"contexto {_tokens(used.value)}/{_tokens(win.value)} ({word})")
    elif used.known:
        extra = f" · máx. do modelo {_tokens(s.model_max_context.value)}" if s.model_max_context.known else ""
        parts.append(f"contexto {_tokens(used.value)} ({STATE_WORD[used.state]}), janela não informada{extra}")
    else:
        parts.append("contexto não informado pelo provedor")
    if s.input_tokens.known and s.output_tokens.known:
        parts.append(f"turno {_tokens(s.input_tokens.value)} entrada / {_tokens(s.output_tokens.value)} saída "
                     f"({STATE_WORD[s.output_tokens.state]})")
    si, so, sc = s.session_input_tokens, s.session_output_tokens, s.session_cache_tokens
    if si.known and so.known:
        cache = f" / {_tokens(sc.value)} cache" if sc.known else ""
        parts.append(f"sessão, modelo principal: {_tokens(si.value)} entrada / {_tokens(so.value)} saída{cache} "
                     f"({STATE_WORD[so.state]})")
    bg = s.session_background_tokens
    if bg.known and bg.value:
        names = bg.source.split("modelos de fundo: ", 1)[-1] if "modelos de fundo: " in bg.source else "?"
        parts.append(f"fundo {_tokens(bg.value)} ({names}; {STATE_WORD[bg.state]}, fora do total acima)")
    windows = [(label, getattr(s, name), label == "7d") for label, name in (("5h", "account_5h_utilization"),
                                                                            ("7d", "account_7d_utilization"))]
    shown = [f"{label} {round(m.value * 100)}%{_resets(m.resets_at, tz, date)}" for label, m, date in windows if m.known]
    if shown:
        state = next(m.state for _l, m, _d in windows if m.known)
        parts.append(f"conta: {' · '.join(shown)} ({STATE_WORD[state]})")
    c = s.cost_api_equivalent_usd
    if c.known:
        parts.append(f"custo equivalente de API US$ {c.value:.2f} ({STATE_WORD[c.state]}, não é fatura)")
    line = " · ".join(parts)
    return f"{line} · {s.stale_reason}" if s.stale_reason else line
