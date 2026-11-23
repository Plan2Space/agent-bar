"""usage.py — telemetria de tokens e contexto com estado por campo (SPEC P2S-STUDIO-WORKSPACE-UX, F6).

Cada valor diz de onde veio e quanto vale como prova:
- MEASURED: o provedor informou o numero (ex.: `eval_count` do Ollama, `usage_update.used` do ACP);
- ESTIMATED: calculado aqui ou pelo provedor sem ser medicao do que foi cobrado/usado (ex.: custo equivalente de API
  numa assinatura; contexto do Ollama somado dos contadores do turno);
- UNAVAILABLE: o provedor nao informa. Nunca vira zero;
- STALE: era MEASURED/ESTIMATED, mas deixou de valer (troca de modelo, ou a atualizacao seguinte nao trouxe o campo).

Regras: estimativa nunca aparece como medicao; o maximo do modelo (`/api/show`) nao e a janela da sessao; custo
equivalente de API nao e fatura; limite da conta nao e informado por nenhum provedor atual (UNAVAILABLE).
O que cada provedor informa de fato esta medido em agent-bar (F6, achados de 2026-11-23).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

STATES = ("MEASURED", "ESTIMATED", "UNAVAILABLE", "STALE")
METRICS = ("input_tokens", "output_tokens", "context_used", "context_window", "model_max_context",
           "cost_api_equivalent_usd")
SCHEMA = "usage-snapshot.v1"


@dataclass(frozen=True)
class Metric:
    value: float | int | None
    state: str
    source: str = ""

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
    stale_reason: str = ""

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"schema": SCHEMA, "provider_id": self.provider_id, "model_id": self.model_id,
                               "stale_reason": self.stale_reason}
        for name in METRICS:
            m: Metric = getattr(self, name)
            out[name] = {"value": m.value, "state": m.state, "source": m.source}
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> UsageSnapshot:
        if d.get("schema") != SCHEMA:
            raise ValueError(f"schema inesperado: {d.get('schema')!r}")
        metrics = {n: Metric(d[n]["value"], d[n]["state"], d[n].get("source", "")) for n in METRICS}
        return cls(str(d.get("provider_id", "")), str(d.get("model_id", "")), stale_reason=str(d.get("stale_reason", "")),
                   **metrics)

    def stale(self, why: str) -> UsageSnapshot:
        """Tudo que era conhecido vira STALE (ex.: troca de modelo); o que era UNAVAILABLE continua."""
        changes = {n: replace(getattr(self, n), state="STALE") for n in METRICS if getattr(self, n).known}
        return replace(self, stale_reason=why, **changes)

    def merged_with(self, newer: UsageSnapshot) -> UsageSnapshot:
        """A atualizacao seguinte vence; campo que ela nao traz mantem o valor anterior, marcado STALE."""
        if (newer.provider_id, newer.model_id) != (self.provider_id, self.model_id):
            return newer
        changes = {}
        for n in METRICS:
            old, new = getattr(self, n), getattr(newer, n)
            if not new.known and old.known:
                changes[n] = replace(old, state="STALE")
        return replace(newer, **changes) if changes else newer


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
    return UsageSnapshot(
        provider_id, model_id,
        context_used=Metric(used, "MEASURED", "acp usage_update.used") if used is not None else unavailable("acp: sem used"),
        context_window=Metric(size, "MEASURED", "acp usage_update.size") if size else unavailable("acp: sem size"),
        cost_api_equivalent_usd=(Metric(amount, "ESTIMATED", "acp usage_update.cost (preco equivalente de API)")
                                 if amount is not None else unavailable("acp: custo nao informado neste update")),
    )


# ------------------------------------------------------------------------------------------------ exibicao
STATE_WORD = {"MEASURED": "medido", "ESTIMATED": "estimado", "STALE": "desatualizado"}


def _tokens(v: float) -> str:
    v = int(v)
    if v >= 10_000:
        return f"{round(v / 1000)}k"
    return f"{v / 1000:.1f}k".replace(".0k", "k") if v >= 1000 else str(v)


def usage_line(s: UsageSnapshot | None) -> str:
    """Uma linha para o Chat. Diz o estado de cada numero; o que nao foi informado aparece como tal."""
    if s is None:
        return "uso: não informado pelo provedor"
    parts = []
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
    c = s.cost_api_equivalent_usd
    if c.known:
        parts.append(f"custo equivalente de API US$ {c.value:.2f} ({STATE_WORD[c.state]}, não é fatura)")
    line = " · ".join(parts)
    return f"{line} · {s.stale_reason}" if s.stale_reason else line
