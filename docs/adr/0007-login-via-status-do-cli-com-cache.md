# ADR 0007: Login do Claude/Codex lido do `status` do CLI, em cache

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
Para a cor "conectado" fazer sentido ([ADR 0002](0002-cor-igual-conexao-logo-igual-trabalho.md)), a barra precisa saber se o Claude Code e o Codex estão **logados**, não só se existem processos. Ler arquivos de credencial é frágil (formatos mudam) e sensível (contêm tokens).

## Decisão
1. A barra pergunta ao **próprio CLI**: `claude auth status` (JSON, campo `loggedIn`) e `codex login status` ("Logged in" / "Not logged in").
2. O resultado é `True`, `False` ou `None` (desconhecido: CLI ausente, erro, saída ilegível). **`None` nunca vira "logado".**
3. Cache de **300 s** para valores conhecidos e **30 s** para `None` (recupera logo após uma falha passageira). Timeout de 25 s por chamada, sem janela, com ambiente limpo (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `GH_TOKEN` removidos).
4. A barra **nunca lê, guarda ou exibe** tokens ou arquivos de credencial.
5. O login é um **parâmetro opcional** dos coletores (`login=None` por padrão): sem ele, valem os estados conservadores antigos (útil em testes e no diagnóstico sem rede).

## Alternativas consideradas
- **Ler `~/.claude/.credentials.json` / `~/.codex/auth.json`:** descartada: expõe segredos ao processo da barra e depende de formato interno.
- **Deduzir login por processo vivo:** descartada: um app aberto não prova login (foi exatamente o erro que gerou o azul indevido do Codex).
- **Consultar a cada ciclo (3 s):** descartada: ~1 s por CLI, a cada 3 s.

## Consequências
- ✅ Verde significa "logado de verdade"; vermelho "Sem login" dá a instrução (`claude auth login`, `codex login`).
- ✅ Nenhum segredo passa pela barra.
- ⚠️ Até 5 minutos de atraso para refletir um login/logout recente (o botão "Atualizar" **não** invalida este cache).
- ⚠️ Depende do formato de saída dos CLIs (por isso o parsing é defensivo e cai em `None`).

## Evidência
- `test_cli_login_state_is_read_from_the_cli_and_cached`
- `test_claude_logged_in_is_green_connected_even_without_an_orchestrator_or_sessions`, `test_claude_without_login_is_red_and_not_installed_is_gray`, `test_an_unknown_login_keeps_the_previous_conservative_states`
- `test_codex_without_login_is_red_and_logged_in_without_processes_is_green`
- Fontes em [Fontes de dados](../architecture/02-fontes-de-dados.md).
