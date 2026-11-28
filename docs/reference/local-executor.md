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

O executor sempre pede pelo canal de permissão do Chat (o mesmo do ACP) e não tem caminho próprio de aprovação: vale o
que esse canal decidir e registrar. Recusar, fechar ou deixar vencer = zero chamada ao motor. Um pedido = no máximo um run (registro em
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

Fechar a aba ou trocar de agente durante o build não congela a janela: o motor termina sozinho e o resultado fica no
registro. Sair do Studio nesse momento deixa o processo invisível até o motor acabar (no máximo o prazo do motor, 15
min); reabrir o Studio não espera por isso e o registro reconcilia o run.

## Capacidade honesta

`tool_calls` do adaptador Ollama: `UNSUPPORTED` no perfil Conversa; `UNKNOWN` no Executor até uma chamada terminar
`completed`; só então `SUPPORTED`, com a prova (modelo@digest → ferramenta → run). Por modelo:
`OllamaAdapter.tool_calls_for(modelo)` diz `SUPPORTED` só para o `modelo@digest` que executou; outro modelo continua
`UNKNOWN`.

## Proveniência por papel (LAB-04)

Cada execução grava em `<saída>/_studio/<request_id>.json` o bloco `provenance` (`p2s.lab.provenance.v1`): provedor,
adaptador, versão do provedor, `model_id`, `model_digest`, sha256 do prompt do envio, parâmetros de inferência pedidos e
os papéis separados. No pedido só existem `tool_requester` (o modelo local) e `outcome_writer` (o Studio, pelo
commit); `planner`, `builder` e `evaluator` (o motor, pelo commit; o modelo só escolheu a ferramenta e a
`fixture_ref`) ficam em `roles_expected` e só passam a `roles` quando o `run_manifest` do motor registra o passo OK
(o avaliador exige também o `validation_report.json`). Timeout, falha antes do passo ou recusa não creditam nada ao
motor. `renderer` e `refiner` ficam `null` nesta ferramenta. Não há modelo de reserva (`fallback_model` é sempre `null`). O que não foi medido fica `null`,
nunca um valor inventado (ex.: `model_version`, `experiment_id`, `parent_run_id`).

## Descoberta dinâmica e identidade por modelo (LAB-10)

Nenhuma lista de modelos no código: o adaptador lê `GET /api/tags` + `POST /api/show` ao conectar e
`OllamaAdapter.refresh_models()` relê sem desconectar, devolvendo `added`, `removed` e `digest_changed` (mesmo nome,
outro blob). A prova de `tool_calls` é por `modelo@digest`: um re-pull com digest novo volta a `UNKNOWN`.
`OllamaAdapter.model_identity(modelo)` (`p2s.model_identity.v1`) separa o que o servidor **anuncia** (tools, visão,
thinking, contexto) do que foi **comprovado** neste Studio. `model_version` fica `null`: o Ollama só fornece a tag
(dentro do id) e o digest. `P2S_LOCAL_EXECUTOR_MODEL` é só preferência; se não estiver instalado, vale o primeiro
modelo instalado que anuncia `tools`.

Negativos (LAB-22): modelo inexistente é recusado antes de qualquer pedido; modelo removido no meio da sessão falha
tipado sem executar nada; resposta em nome de outro modelo (`done.model` diferente do pedido) é recusada com
`MODEL_MISMATCH` e não executa ferramenta.

## Render pelo modelo local (LAB-16)

Quando o motor tem a fixture `plan_alt_5x3_e3a` (sala 5x3 com cama, no motor `sketchup-mcp-next`) e a render_spec de
amostra, o perfil publica uma segunda ferramenta, `render_room_sample(run_id, cameras)`:

- o modelo só nomeia um `run_id` de build `completed` deste executor com render_spec, e 1 ou 2 câmeras da spec;
  `.skp`, render_spec, pasta e slot são do Studio;
- sem slot concedido (`P2S_LOCAL_EXECUTOR_RENDER_SLOT`, dado pelo coordenador) = `SLOT_NOT_GRANTED`, sem perguntar nem
  abrir nada; a saída do executor precisa ficar sob `<motor>/runs` (raiz que o render do motor aceita);
- o slot é uma variável de ambiente lida a cada chamada: qualquer valor não vazio vale como "concedido" enquanto o
  processo do Studio viver. A auditoria é o próprio valor, mostrado no cartão de permissão (`rawInput.slot`) e gravado
  pelo motor em `render_manifest.request.slot`; quem concede e quando fica no canal do coordenador, não no Studio;
- mesma permissão, mesmo lock e mesma recusa com SketchUp aberto do build (um motor por vez, build ou render);
  o `.skp` é re-conferido pelo sha256 do build antes e depois da permissão;
- chama `python -m p2s_engine.render <spec> <skp> --out-dir --run-id --cameras --slot --request-id --parent-run-id
  --requested-by-json` (contrato da Frente A; a CLI com esses argumentos é pré-requisito no motor);
- `completed` só com `render_manifest` OK + veredito PASS + cada PNG dentro do run, não vazio e com o sha256
  registrado pelo motor; visual sempre pendente (humano). O papel `renderer` só entra quando o motor gravou quadros, e
  `parent_run_id` liga o render ao build.
- coerção estrita de `array` vindo como string JSON (prova pós-merge de 2026-11-27: o llama3.1:8b mandou
  `"cameras": "[\"amostra_perspectiva\", \"amostra_lateral\"]"`). O Studio só aceita a string quando o parâmetro é
  `array` no schema publicado e ela decodifica para uma lista que passa no mesmo schema: itens `string` do enum,
  `minItems`, `maxItems` e `uniqueItems`. Qualquer outra coisa continua `CAMERA_NOT_ALLOWED`: texto que não é JSON,
  JSON que vira objeto, câmera fora da spec, mais de 2 câmeras ou câmera repetida. A coerção fica registrada como
  `argument_coercion: [{param, raw, used}]` no cartão de permissão (`rawInput`), no resultado gravado e no texto do
  cartão. O `call_digest`/`request_id` continua sendo o do que o modelo mandou;
- toda recusa leva o nome da ferramenta pedida (build ou render) e os argumentos que o modelo mandou
  (`model_arguments`, também no texto do cartão), para ser auditável sem o log do driver.

## Guardas de borda do motor (`--via tool-cli`)

O build chama `python -m p2s_engine.run … --via tool-cli` (no motor `sketchup-mcp-next`): o próprio motor recusa, antes de
qualquer I/O e antes de abrir o SketchUp, fixture fora de `tests/fixtures/`/`runs/`, saída fora de `runs/`, `run_id`
fora do padrão e SketchUp/V-Ray já abertos. Nesse caso não há `run_manifest`; o motor deixa
`{status: BLOCKED, error: {code, message}}` no stdout, que o executor grava em `<request_id>.engine.stdout.json` (o
stderr fica no `.engine.log`) e registra com o código exato (`PATH_NOT_ALLOWED`, `SKETCHUP_BUSY`, `VRAY_BUSY`,
`BUSY_UNKNOWN`), não como `NO_MANIFEST`. Desde o #72 @4f1a799, SketchUp/V-Ray ocupado é conferido por último,
depois da validação: aí há `run_manifest` com status `SKETCHUP_UNAVAILABLE` e `error.code` exato, e o executor registra
esse código (o SketchUp não abriu; nenhum papel de construção é creditado). O mesmo vale para o render.

## Benchmark de compatibilidade modelo × motor (C0–C9)

`python -m agent_bar.platform.compat_bench --model llama3.1:8b --model deepseek-r1:14b --cases C0,C1,C2 --out <pasta>`
mede se cada modelo local OPERA o mesmo motor pelas mesmas ferramentas do Studio. Não mede se o modelo "gera
geometria": construção, validação, `.skp` e render são do motor.

- Mesmo caminho do produto (`executor_adapter`: OllamaAdapter + ToolBox). Todos os modelos recebem as mesmas
  ferramentas, as mesmas descrições, o mesmo system prompt, as mesmas opções de inferência (`temperature 0`, `seed 42`,
  via `OllamaAdapter.inference_options`, vazio no Studio) e a mesma permissão (auto `allow_once` pelo runner, como o
  modo bypass). Tudo isso fica em `conditions`, com sha256 das ferramentas e do system prompt.
- Cada caso roda numa conversa nova, com texto de prompt fixo. O critério sai do que chegou ao executor e do resultado
  do motor, nunca da frase do modelo.
- Status: `PASS`, `FAIL` (erro do modelo: ferramenta, argumento ou fixture errada, nenhuma chamada, chamada a mais,
  recusa da validação do Studio), `INFRA_ERROR` (chamada certa, mas o motor, a máquina ou o provedor falhou: não conta
  contra o modelo), `BLOCKED` (falta ferramenta no motor; motivo e dono), `NOT_RUN` e `NOT_SUPPORTED` (o modelo não
  anuncia tools).
- Sem Claude como fallback. `assistance` registra qualquer ajuda externa e, hoje, é sempre vazio.
- Os casos BLOCKED citam a lacuna exata (`gaps`) da matriz do motor (`contracts/tool_compat_matrix.json`,
  no motor `sketchup-mcp-next`), com o título lido da matriz do motor fixado. `conditions.matrix` registra se ela existe
  e o sha256 dela.
- Resultado por eixo (`operation`, `planning`, `layout`), sem nota única. Cada modelo gera
  `bench-<stamp>-<modelo>/benchmark_run.json` (`p2s.mcp_compat.run.v1`) e nunca sobrescreve um run anterior.

| Caso | Eixo | Hoje |
|---|---|---|
| C0 Descoberta | operação | listar as ferramentas publicadas sem chamar nenhuma e sem inventar outra (se chamar, a permissão é negada: nada abre) |
| C1 Chamada simples | operação | `build_room_sample(room_001)`, com nome e argumento dados no pedido |
| C2 Construção existente | operação | "sala de 5 x 3 metros com cama" → o modelo escolhe `plan_alt_5x3_e3a` pela descrição |
| C3 Parametrização | operação | BLOCKED, G1 (write_fixture fora do motor; Frente A) |
| C4 Sequência, C5 Recuperação, C6 Render | planejamento/operação | NOT_RUN (próximos) |
| C7 Refinamento | planejamento | BLOCKED, G4 e G10 (sem refinamento e sem troca de material; Frente A, `material_overrides` no motor) |
| C8 Fluxo até evidência | planejamento | BLOCKED, G5 e G3 (publicação e inspeção avulsa fora do motor; Frentes A e C) |
| C9 Suíte 01 | layout | BLOCKED, G2 (a Suíte 01 rc7b/rc8 existe no motor, mas não na allowlist publicada; falta `list_fixtures` no motor) |

## Interface com o roteador Local First (`local_job`)

Contrato combinado com a Frente E (agent-runtime#72/#73). A fronteira é um arquivo, e o agent-runtime não importa o
agent-bar:

`python -m agent_bar.platform.local_job run <job.json> --out <dir>` grava `<dir>/<job_id>/outcome.json`, o arquivo
que o roteador lê. Ele é sempre o desfecho da execução **mais recente** daquele `job_id`, porque rodar de novo o
mesmo `job_id` é caminho previsto (retomar na fila, `<tarefa>-a<tentativa>` no runtime):

- antes de executar, o desfecho anterior sai do caminho e vai para o histórico `outcome.<n>.json`, então o roteador
  não lê um desfecho velho enquanto a execução nova roda;
- o novo entra por escrita atômica (arquivo temporário + rename);
- se a execução quebra, entra um `FAILED` com `INFRA:RUNNER_ERROR`: uma falha nunca deixa um sucesso antigo no lugar;
- falha atribuível ao executor vai também para `<dir>/failures.jsonl`.

A CLI e a fila de produção gravam pelo mesmo helper (`local_job.record_outcome`).

- Job `p2s.local_job.v1` e desfecho `p2s.local_outcome.v1`. Os JSON Schemas estão em `docs/contracts/`.
- `executor.kind = deterministic`: o motor roda direto, sem LLM e com zero token. Na proveniência, `tool_requester`
  vira `runtime`. `executor.kind = local`: um modelo do Ollama opera as mesmas ferramentas do Studio, com as opções de
  inferência do benchmark. O `feedback` do roteador entra no pedido da tentativa seguinte e fica em `assistance`.
- Pino do motor: o job é `REFUSED` com `ENGINE_NOT_PINNED` se o motor configurado não é o checkout de produção
  (`P2S_PRODUCTION_ENGINE`), se o HEAD difere de `engine_ref.sha40` ou se o checkout está sujo. Trocar de versão é um job
  explícito com outro sha.
- O desfecho sai do que chegou ao executor e do resultado do motor:
  - `SUCCEEDED`;
  - `TOOL_ERROR` com `PROTOCOL` (nome fora do publicado, JSON quebrado), `SCHEMA` (argumento fora do contrato) ou
    `CONTEXT` (chamada válida, mas não a do job);
  - `FAILED/CAPABILITY` (nenhuma chamada);
  - `FAILED` ou `TIMEOUT` com `INFRA:<código>` (motor, máquina ou provedor; não conta contra o executor);
  - `REFUSED`.
- Sem provedor cloud no caminho: `cloud = {tokens: 0, providers_called: []}`. `tokens_measured` vem dos contadores do
  Ollama (0 no determinístico). `tokens_estimated` fica `null`. Para isso valer, o job local é recusado quando o
  modelo é um modelo cloud do Ollama (`…:cloud`, `…-cloud`; job inválido) ou quando o endpoint do Ollama não é
  loopback (`REFUSED` com `NOT_LOCAL`): nos dois casos o modelo rodaria fora da máquina sem registro de nuvem.
- Prova por (executor, task_type): `python -m agent_bar.platform.capability_proofs <pastas> --out
  <pasta>/capability_proofs.json` agrega `benchmark_run.json` e `outcome*.json` em `p2s.local_capability.v1`:
  - `success_rate = passes/(passes+fails)`, com INFRA fora do denominador; `null` quando não há prova;
  - job `REFUSED` não conta como execução;
  - `failures.jsonl` sai ao lado, em `p2s.local_failure.v1`, com todas as falhas.
