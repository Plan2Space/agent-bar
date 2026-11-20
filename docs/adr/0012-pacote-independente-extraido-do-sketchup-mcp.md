# ADR 0012: Pacote independente extraído do `sketchup-mcp`

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
O Agent Bar nasceu dentro do repositório `GFCDOTA/sketchup-mcp` (`tools/orchestration/agent_bar`, frentes FP-058 e FP-061), onde convive com um pipeline de geração de `.skp` e com a orquestração FP-060. O dono criou um repositório próprio (`GFCDOTA/agent-bar`, **público**) para publicá-lo e evoluí-lo separadamente.

## Decisão
1. O código foi **copiado** para `agent_bar/` na raiz do novo repositório, com os imports renomeados de `tools.orchestration.agent_bar` para `agent_bar`. O pacote **só importa de si mesmo**.
2. Os testes foram levados junto e **rodam sem o `sketchup-mcp`**. A única dependência, o `EventStore` (usado só para criar o banco de fixture), foi **copiada** para `tests/support/event_store.py` (somente biblioteca padrão).
3. O código ainda **observa** o ecossistema do `sketchup-mcp` (nome da tarefa, repositório e Project, padrão do laço de revisão): esses acoplamentos são **conhecidos e documentados**, e o roadmap prevê torná-los configuráveis.
4. O trabalho de desenvolvimento da FP-061 continua, por ora, no *worktree* do `sketchup-mcp`; mudanças são levadas para o novo repositório (a branch de integração é `develop`).
5. Higiene de repositório público: varredura de segredos, e-mail, caminhos pessoais e dados de produção **antes** de cada publicação; commits com e-mail *noreply* do GitHub; capturas só de fixture rotulada.

## Alternativas consideradas
- **Submódulo ou subárvore do `sketchup-mcp`:** descartada: acopla o ciclo de vida e o histórico.
- **Publicar sem testes ou com o repositório inteiro:** descartada: o pacote precisa ser verificável sozinho.
- **Reescrever os testes para não precisar do EventStore:** descartada: o EventStore real é a melhor fixture do contrato lido; vendê-lo como cópia é mais simples e fiel.
- **Importar o `EventStore` do `sketchup-mcp` como dependência:** descartada: criaria uma dependência de um projeto de domínio não relacionado.

## Consequências
- ✅ Instalável, testável e documentável por si só.
- ✅ A extração achou e corrigiu defeitos reais (a raiz padrão de `default_gov_dir` calculada pelo layout antigo; e uma variável documentada que não existia).
- ⚠️ **Duas cópias** do código até a convergência: é preciso sincronizar (e a cópia do `EventStore` pode divergir).
- ⚠️ Os acoplamentos listados nas [limitações](../limitacoes-e-roadmap.md) significam que, hoje, a barra só mostra dados relevantes para o ecossistema de origem.

## Evidência
- Suíte completa (145 testes) verde no repositório independente; `ruff` limpo.
- `tests/test_standalone_layout.py` (a raiz padrão fica dentro deste repositório).
