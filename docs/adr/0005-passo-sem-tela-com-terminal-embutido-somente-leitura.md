# ADR 0005: Passo do Claude sem tela, com terminal embutido somente leitura

> **Status: substituída em 2026-11-22.** A peça desta decisão saiu do Agent Bar junto com o orquestrador legado FP-057/FP-060 ([`docs/audit/legacy-cleanup-2026-11-22.md`](../audit/legacy-cleanup-2026-11-22.md)). Mantida como histórico.

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
O dono quis um botão (na barra e no painel) que desse **andamento** nos PRs: uma sessão do Claude que corrige achados de revisão. A primeira implementação **abria um terminal separado** (Windows Terminal) com o Claude Code **interativo**. Dois problemas apareceram no uso real:
1. **A janela externa tirava o dono do contexto** ("tem como fazer um terminal dentro do Agent Control, para evitar ficar indo para outra tela?").
2. A sessão interativa aberta pela barra **caiu na tela de login** (OAuth) na primeira vez, deixando uma janela parada. Já o modo sem tela (`claude -p`) funcionou com a mesma conta.

## Decisão
1. O passo roda como **processo filho do painel** (`QProcess`) com `claude -p ... --permission-mode auto --output-format stream-json --verbose`, sem janela de console e com o stdin fechado.
2. A barra **traduz** o `stream-json` em texto legível (`format_stream_line`) e o mostra num **terminal embutido, somente leitura** (até 500 linhas).
3. O botão alterna **Dar andamento ⇄ Parar passo** (e ▶ ⇄ ■ na barra). Parar encerra **só a árvore do PID** do passo (`taskkill /T /F /PID`).
4. Antes de iniciar: confirmação, recusa em modo somente leitura, recusa se há passo em outra janela, e recusa sem CLI ou sem arquivo de missão.
5. O ambiente do filho **não herda** `GH_TOKEN`, `CLAUDECODE` nem `CLAUDE_CODE_ENTRYPOINT`.

## Alternativas consideradas
- **Terminal externo interativo (versão inicial):** descartada: troca de tela e falha de login no primeiro uso.
- **Terminal embutido interativo (pty/ConPTY):** descartada por ora: muito mais complexo (emulação de terminal, entrada, resize) e amplia a superfície de risco; o dono precisa **acompanhar e parar**, não digitar. Fica no [roadmap](../limitacoes-e-roadmap.md).
- **Executar via `--execute` do executor:** descartada ([ADR 0001](0001-somente-observacao-com-uma-unica-acao.md)).

## Consequências
- ✅ Tudo no mesmo lugar: barra, painel e progresso; um clique para iniciar e outro para parar.
- ✅ Saída legível e filtrada (texto, ferramentas e resultado) em vez de JSON cru.
- ⚠️ **Não é interativo:** não se digita no passo.
- ⚠️ O passo roda com as credenciais do dono em modo `auto`: o que ele faz depende da **missão** e do classificador do Claude, não da barra ([Segurança](../seguranca.md)).
- ⚠️ Parar interrompe o processo, **não desfaz** o que já foi publicado.

## Evidência
- `test_progress_button_runs_the_step_inside_the_panel_and_streams_the_output`
- `test_the_stop_button_ends_only_the_step_it_started` (confere que o PID morreu)
- `test_stream_lines_become_readable_terminal_lines`, `test_progress_command_runs_claude_headless_in_auto_mode_from_the_workspace`
- `test_a_declined_confirmation_starts_nothing`, `test_a_step_already_running_in_another_window_blocks_a_second_one`, `test_a_missing_claude_cli_is_reported_not_faked`
- `test_the_bar_has_a_play_button_that_runs_the_same_step_and_turns_into_stop`
- Detalhes em [Passo do Claude](../reference/passo-claude.md).
