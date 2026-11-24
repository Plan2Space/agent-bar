"""roles.py — o papel de cada agente do Chat e as capacidades que estão VERIFICADAS (UX-11; D-UX-4 emendada pela SPEC
da PoC do executor local, es#81 §11). Sem Qt.

Papéis:
- **Conversa**: Ollama genérico e a demonstração. Sem ferramentas: não abre SketchUp, não constrói, não renderiza.
- **Executor local (amostra)**: uma ferramenta só, `build_room_sample` (P2S-LOCAL-EXECUTOR).
- **Executor Claude**: Claude Code via ACP; as ferramentas são as do Claude Code, e o MCP só conta quando conferido.
- **Agente ACP**: outro agente ACP configurado; capacidades não conferidas, nada é prometido.

Regra (do coordenador, reafirmada no UX-11): nenhum texto afirma capacidade que o agente não tem, e nenhum prompt de
sistema "ensina" o modelo a fingir. Pedido de execução numa Conversa é recusado AQUI, antes do envio, com o motivo e a
oferta do Executor; mandar mesmo assim é uma escolha explícita ("Enviar só como conversa").
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from agent_bar.cockpit.health import EXECUTOR_KEYS   # uma fonte só: quem executa ferramentas (Claude ACP)

CONVERSA = "Conversa"
EXECUTOR_LOCAL = "Executor local (amostra)"
EXECUTOR_CLAUDE = "Executor Claude"
ACP_OTHER = "Agente ACP"


def role_of(profile) -> str:
    adapter = getattr(profile, "adapter", "acp")
    if adapter == "ollama-executor":
        return EXECUTOR_LOCAL
    if adapter == "ollama" or getattr(profile, "demo", False):
        return CONVERSA
    if adapter == "acp" and getattr(profile, "key", "") in EXECUTOR_KEYS:
        return EXECUTOR_CLAUDE
    return ACP_OTHER


def capabilities(profile, tools: Iterable[str] = (), mcp: str = "") -> str:
    """O que o agente FAZ, só com o que está conferido. `tools` = ferramentas vistas nesta sessão; `mcp` = estado do MCP
    conferido (vazio = não conferido)."""
    role = role_of(profile)
    if role == CONVERSA:
        return "sem ferramentas (demonstração, sem LLM)" if getattr(profile, "demo", False) else "sem ferramentas"
    if role == EXECUTOR_LOCAL:
        return "uma ferramenta: build_room_sample (amostra no SketchUp)"
    if role == EXECUTOR_CLAUDE:
        used = sorted(set(tools))
        parts = ["ferramentas do Claude Code"]
        if used:
            parts.append(f"usadas nesta sessão: {len(used)}")
        parts.append(f"MCP: {mcp}" if mcp else "MCP não conferido")
        return " · ".join(parts)
    return "capacidades não conferidas"


def label(profile, tools: Iterable[str] = (), mcp: str = "") -> str:
    return f"{role_of(profile)} · {profile.label} — {capabilities(profile, tools, mcp)}"


# Pedido de EXECUÇÃO: um verbo de ação + um objeto do motor. Pergunta sobre a ferramenta ("o que é o SketchUp?") não é.
_ACTION = (r"\b(abr[ea]|abrir|mont[ae]|montar|constr[oó]i|construir|cri[ae]|criar|ger[ae]|gerar|render(iz[ae]|izar)?|"
           r"rod[ae]|rodar|execut[ae]|executar|faz|fazer|mobili[ae]|mobiliar|refaz|refazer|edit[ae]|editar|salv[ae]|salvar)\b")
_OBJECT = (r"(sketchup|\.skp\b|\bskp\b|build_room|render_room|\brender\b|\bc[oô]modo\b|\bsu[ií]te\b|\bplanta_?\d*|"
           r"\bcama\b|\bmarcenaria\b|\bv-?ray\b)")
_TOOL_CALL = r"\b(build_room(_sample)?|render_room|validate_room_fixture)\b"


def wants_execution(text: str) -> bool:
    t = text.lower()
    if t.rstrip().endswith("?") and not re.search(_TOOL_CALL, t):
        return False                                                            # pergunta: conversa
    if re.search(_TOOL_CALL, t):
        return True
    if re.search(r"\brenderiz", t):                                             # "renderiza X" já é execução
        return True
    return bool(re.search(_ACTION, t) and re.search(_OBJECT, t))


def refusal(profile, executors: Sequence) -> str:
    names = [role_of(p) for p in executors]
    offer = (" Use " + " ou ".join(dict.fromkeys(names)) + ".") if names else " Nenhum executor está disponível agora."
    return (f"{profile.label} é {CONVERSA} — sem ferramentas: não executa SketchUp, build ou render.{offer} "
            "Nada foi enviado ao modelo; o texto voltou para a caixa.")


__all__ = ["ACP_OTHER", "CONVERSA", "EXECUTOR_CLAUDE", "EXECUTOR_KEYS", "EXECUTOR_LOCAL", "capabilities", "label",
           "refusal", "role_of", "wants_execution"]
