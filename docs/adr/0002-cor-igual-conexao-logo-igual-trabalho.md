# ADR 0002: Cor da bolinha = conexão; logo animado = trabalhando

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
A primeira versão misturava três ideias na mesma bolinha: **conectado** (verde), **executando** (azul), **aguardando/ocioso** (âmbar) e **desligado/sem integração** (cinza). Na prática, a barra mostrava Claude e Codex azuis só porque havia um processo aberto, GPT e Listener cinza, GitHub verde, e o dono relatou: *"não faz sentido: uma cinza, as outras azul e verde"*. O azul aparecia para o app do Codex apenas aberto (sem trabalho nenhum) e o cinza do Claude não significava "desconectado" (significava "sem orquestrador").

## Decisão
1. A **cor** da bolinha responde a **uma** pergunta: *este agente está conectado?*
   - verde = conectado; cinza = desconectado/sem CLI/sem integração/sem dado; âmbar = atenção; vermelho = erro.
2. Quando o agente está **trabalhando** (`Level.BLUE`), a bolinha é **substituída pelo logo animado do próprio agente**. Não existe bolinha azul na barra.
3. "Trabalhando" é definido por **evidência concreta**: processo filho do executor, passo do botão em curso, ou `codex exec`/`review` ativo. Um app aberto **não** é trabalho.
4. O painel mostra uma **legenda** e usa o glifo `✻` (na cor do agente) onde a barra mostra o logo.

## Alternativas consideradas
- **Manter azul para "executando":** descartada: mantém duas escalas na mesma bolinha e o azul continuava ambíguo.
- **Anel/halo ao redor da bolinha quando trabalha:** descartada: menos reconhecível que o logo e o dono pediu explicitamente o logo.
- **Uma cor por agente (identidade) e o estado por brilho:** descartada: perde a leitura imediata verde/cinza/âmbar/vermelho.

## Consequências
- ✅ Regra única, memorizável; a legenda cabe numa linha.
- ✅ Logo animado chama atenção só quando algo acontece, e a animação só roda enquanto houver trabalho (sem custo de CPU em repouso).
- ⚠️ Estados antigos passaram a ter outro nome/cor (por exemplo, "Ocioso" âmbar virou "Conectado" verde quando o login é conhecido).
- ⚠️ A semântica depende do login conhecido ([ADR 0007](0007-login-via-status-do-cli-com-cache.md)); sem ele a barra usa estados conservadores.

## Evidência
- `test_idle_agents_show_a_colored_dot_and_working_agents_show_their_animated_logo`
- `test_each_agent_has_its_own_logo_painter_and_the_animation_advances`
- `test_the_panel_explains_the_colors_and_marks_working_agents_with_a_glyph`
- `test_codex_app_open_is_not_working_it_is_connected_when_logged_in`
- Regras completas em [Estados dos agentes](../reference/estados-dos-agentes.md).
