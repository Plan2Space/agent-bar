# ADR 0006: A barra pode ficar sobre a barra de tarefas (monitor inteiro + topo reafirmado)

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
A barra era limitada à **área útil** do monitor (a que exclui a barra de tarefas do Windows) e ficava **colada** acima dela. O dono quer a barra **por cima da barra de tarefas**, sempre visível, no lugar que escolher. Há ainda um problema de ordem de janelas: a barra de tarefas do Windows é "topmost" e **volta para a frente** quando recebe um clique, cobrindo outras janelas "sempre no topo" (inclusive a barra). Houve também um mal-entendido de requisito durante o desenvolvimento (a primeira correção afastou a barra da barra de tarefas, o oposto do pedido); o teste atual fixa o comportamento correto.

## Decisão
1. **Limites da barra = monitor inteiro** (`screen_bounds`, margem 0), não a área útil. Ela pode ser arrastada para cima da barra de tarefas, e nunca para fora do monitor.
2. A **posição salva** é respeitada; se cair fora do monitor (layout de telas mudou), volta para dentro. Em `geometryChanged` de qualquer tela, a barra é reposicionada.
3. A cada **500 ms**, a barra (e o painel, se aberto) **reafirma `HWND_TOPMOST`** com `SetWindowPos(NOMOVE|NOSIZE|NOACTIVATE|NOOWNERZORDER)`: sem roubar foco e sem mover.
4. O **painel continua dentro da área útil** (não cobre a barra de tarefas).

## Alternativas consideradas
- **Manter a barra acima da barra de tarefas (limite pela área útil):** descartada: contraria o pedido.
- **Registrar a barra como *AppBar* (reserva de espaço do Windows):** descartada: reduz a área útil das outras janelas e exige API de shell mais invasiva.
- **Só confiar em `WindowStaysOnTopHint`:** descartada: a barra de tarefas retoma o topo ao ser clicada (comprovado numa simulação).

## Consequências
- ✅ A barra fica onde o dono quiser e volta ao topo em até ~0,5 s depois de a barra de tarefas ser clicada.
- ✅ Ajuste de monitor/resolução não deixa a barra perdida fora da tela.
- ⚠️ A barra pode cobrir ícones da barra de tarefas (a janela é translúcida e arrastável).
- ⚠️ O timer de 500 ms roda sempre (custo desprezível: uma chamada Win32 leve).
- ⚠️ A reafirmação usa Win32 direto (só Windows; fora do Windows é no-op).

## Evidência
- `test_the_bar_can_be_dragged_down_over_the_taskbar_but_never_off_the_monitor`
- `test_a_saved_position_over_the_taskbar_is_kept_and_one_off_the_monitor_is_pulled_back`
- `test_when_the_monitor_changes_the_bar_is_pulled_back_inside_it`
- `test_the_bar_and_the_open_panel_are_raised_above_the_taskbar_periodically`
- Verificado no Windows real (dois monitores): sobreposição medida e retomada do topo ≤ 2 s após trazer a barra de tarefas para a frente.
