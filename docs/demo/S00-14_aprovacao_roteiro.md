# S00-14 (UI): roteiro para o Felipe testar a aprovação humana (~5 min)

> Ação **HIGH**: o botão posta um comentário no GitHub pela sua conta. Siga os passos na ordem. Nada aqui faz merge.
> Detalhes da tela: [`docs/reference/aprovacao-humana.md`](../reference/aprovacao-humana.md).

## Preparar

1. No `launchers\local.env` do checkout que o atalho abre, acrescente `AGENT_BAR_APPROVERS=fmodesto30`. Sem essa linha, o botão fica desabilitado, com o motivo escrito.
2. Feche a barra (clique direito → *Encerrar barra*) e abra de novo pelo atalho **Plan2Space Agent Bar**.

## Ver (não muda nada)

3. Clique na barra → *Central completa ↗* → role até **"Aprovação humana no HEAD exato (S00-14)"**.
4. Confira, para cada PR MEDIUM/HIGH aberto:
   - o **risco** e os motivos, que vêm do `classify_risk` do runtime;
   - o **HEAD completo**, o **CI** e a **evidência** ("não se aplica" fora do motor; no `sketchup-mcp-next`, precisa de execução PASS neste HEAD);
   - o estado: `AGUARDANDO`, `APROVADO no HEAD`, `BLOQUEADO` ou `ERRO`, com o motivo;
   - o **aviso** em amarelo: a mesma conta é usada pelos agentes, então o comentário não prova que foi você.
5. O botão diz qual PR e qual HEAD ele aprovaria (`Aprovar HIGH no HEAD abc123… · agent-bar#NN`). Se estiver desabilitado, o motivo aparece logo abaixo dele.

## Testar a confirmação sem postar

6. Clique no botão: abre a confirmação com o PR, o risco, o SHA completo, o texto exato (`APROVADO <RISCO> <sha>`) e o aviso.
7. Clique **Não**. A linha de status diz "aprovação cancelada: nada foi postado". Confira no GitHub: nenhum comentário novo.

## Aprovar de verdade (só se você quiser aprovar aquele PR)

8. Clique de novo e responda **Sim**. A barra lê o HEAD e o CI outra vez:
   - se alguém deu push depois que a tela foi montada, aparece "recusado: o HEAD mudou de X para Y", e nada é postado;
   - se o CI ficou vermelho, aparece a recusa com o estado do CI.
9. Se o HEAD e o CI continuarem os mesmos, a barra posta **um** comentário `APROVADO <RISCO> <sha40>` e mostra o link dele no status. Em até 1 minuto o item vira `APROVADO no HEAD`.
10. **Invalidação:** um commit novo nesse PR faz o item voltar para `AGUARDANDO` com o HEAD novo. A aprovação antiga aparece como invalidada.

## Perguntas

- A tela deixa claro **o que** está sendo aprovado (PR, risco, HEAD)?
- O aviso da credencial compartilhada está visível o suficiente? Quer bloquear o botão até existir uma conta própria para os agentes?
- "Evidência não se aplica fora do motor": concorda, ou algum repo deve exigir evidência própria?
