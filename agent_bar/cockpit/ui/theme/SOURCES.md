# Fontes e licenças das paletas

Registro de proveniência, como pede o pedido do Felipe de 2026-11-27 (§4 e §5.2): verificar a licença de cada paleta
"inspirada" antes de incluí-la e nunca copiar código proprietário. Aqui só entram VALORES de cor para os papéis
semânticos do nosso Design System, sem código nem ativo de terceiros.

| Tema(s) | Fonte | Licença | Verificado em |
|---|---|---|---|
| GitHub Light, GitHub Dark, GitHub Dark Dimmed | [primer/primitives](https://github.com/primer/primitives) (tokens de cor do Primer, o design system do GitHub) | MIT © GitHub ([npm](https://npmjs.com/package/@primer/primitives)) | 2026-11-27 |
| P2S Escuro (atual) | as cores que o P2S Studio já usava (agent-bar) | do projeto | 2026-11-27 |
| Alto contraste escuro e claro | original Plan2Space | do projeto | 2026-11-27 |
| Studio Sage, Industrial Black, Walnut Boutique, Bronze Luxury, Concrete, Architectural White, Warm Studio, Graphite | originais Plan2Space | do projeto | 2026-11-27 |

**Aproximação, não cópia:** os temas "GitHub" mapeiam os papéis do Primer (canvas, fg, border, accent, success,
attention, danger) para os nossos. Alguns valores foram ajustados para passar no teste de contraste do catálogo. Não são
o produto GitHub nem usam marca ou ícone dele.

## Fora deste lote, com o motivo

- **Slack:** não encontrei licença que permita reproduzir as paletas oficiais dos temas do Slack. As listas públicas
  (gists, blogs) não declaram licença, e as cores da marca seguem os termos do Slack. Isso fica pendente até haver uma
  fonte com licença. O tema "Nord" para Slack ([nordtheme/slack](https://github.com/nordtheme/slack)) é MIT; quando o
  Nord entrar, a fonte será o próprio [Nord](https://www.nordtheme.com) (MIT).
- **Próximos lotes** (Dracula, Nord, Tokyo Night, Catppuccin, Monokai, Gruvbox, One Dark, Solarized, Material, Darcula):
  cada um entra com a licença conferida e registrada nesta tabela. Sem licença clara, o tema não entra.

Nenhuma dependência nova foi adicionada para o Design System: só PySide6 (já no projeto).
