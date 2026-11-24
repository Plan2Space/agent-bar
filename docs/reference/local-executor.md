# Executor local (amostra) — modelo do Ollama constrói uma amostra no SketchUp

SPEC: `Plan2Space/engineering-specs` → `agent-bar/P2S-LOCAL-EXECUTOR-SKETCHUP-POC.md`.

## O que é

Um perfil do Chat, **Executor local (amostra)**, ao lado de **Ollama (local)** (Conversa, sem ferramentas) e do
**Claude Code (ACP)**. Nele o Studio publica para o modelo local UMA ferramenta, `build_room_sample(fixture_ref)`:

1. o modelo devolve uma `tool_call` estruturada (texto que parece JSON não conta);
2. o Studio valida (allowlist `room_001`, `room_alt_5x3`; nada de caminho, URL ou argumento extra) e abre o cartão de
   permissão de sempre (Permission Inbox), com modelo/digest, fixture e hash, `request_id`, `run_id`, pasta de saída e impacto;
3. **Permitir uma vez** roda `python -m p2s_engine.run` do motor `sketchup-mcp-next` (o mesmo `build_room` do MCP
   `p2s-engine`): SketchUp 2026 constrói, salva, fecha o próprio PID, reabre e o inspetor mede;
4. o cartão da ferramenta mostra o que o MOTOR gravou: `run_id`, caminho e SHA256 do `.skp`, veredito da inspeção do
   arquivo reaberto, PIDs do SketchUp. Veredito visual fica PENDENTE (humano);
5. a frase final do turno é escrita pelo Studio a partir desse resultado ("Resultado do motor (escrito pelo Studio, não
   pelo modelo)"), não pelo modelo: no ensaio real o llama3.1:8b resumiu falha como sucesso. O resultado fica no
   histórico como papel `tool`, e o modelo o lê nos turnos seguintes.

O executor sempre pede pelo canal de permissão do Chat (o mesmo do ACP): com o bypass do Studio (ordem do Felipe,
2026-11-24) esse canal aprova sozinho e registra; com "pedir permissão" ligado, recusar, fechar ou deixar vencer = zero
chamada ao motor. Um pedido = no máximo um run (registro em
`<saída>/_studio/requests.sqlite`; prazo vencido = "desconhecido", reconciliado pelo `run_id`, nunca re-executado).
Não abre o SketchUp se outro SketchUp já estiver aberto. Uma rodada de ferramenta por turno.

## Configuração (variáveis de ambiente)

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `P2S_LOCAL_EXECUTOR_ENGINE` | `E:\Claude\apps\sketchup-mcp-next` | checkout do motor (precisa de `.venv`: `uv sync`) |
| `P2S_LOCAL_EXECUTOR_OUT` | `<motor>\runs\local-executor` | onde nascem os runs da amostra (nunca `artifacts\`) |
| `P2S_LOCAL_EXECUTOR_MODEL` | `llama3.1:8b` | modelo preferido (o perfil só lista modelos com `tools`) |
| `P2S_LOCAL_EXECUTOR_ROOTS` | `E:\Claude\apps;E:\Claude\worktrees;E:\Claude\data\runs` | raízes permitidas para motor e saída |

Preflight sem abrir o SketchUp: `python -m agent_bar.platform.sketchup_sample` (JSON `ready`/`reason`).
O perfil aparece indisponível, com o motivo, enquanto o preflight falhar.

## Capacidade honesta

`tool_calls` do adaptador Ollama: `UNSUPPORTED` no perfil Conversa; `UNKNOWN` no Executor até uma chamada terminar
`completed`; só então `SUPPORTED`, com a prova (modelo@digest → ferramenta → run).
