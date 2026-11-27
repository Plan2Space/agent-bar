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
| Dracula | [dracula/dracula-theme](https://github.com/dracula/dracula-theme/blob/main/LICENSE) | MIT | 2026-11-27 |
| Nord | [nordtheme/nord](https://github.com/nordtheme/nord/blob/develop/license) | MIT | 2026-11-27 |
| Tokyo Night, Tokyo Night Storm, Tokyo Night Day | [tokyo-night/tokyo-night-vscode-theme](https://github.com/tokyo-night/tokyo-night-vscode-theme/blob/master/LICENSE.txt) | MIT | 2026-11-27 |
| Catppuccin Latte, Catppuccin Mocha | [catppuccin/catppuccin](https://github.com/catppuccin/catppuccin/blob/main/LICENSE) | MIT | 2026-11-27 |
| Gruvbox Dark, Gruvbox Light | [morhetz/gruvbox](https://github.com/morhetz/gruvbox) (licença declarada no README: MIT/X11; o repositório não tem arquivo LICENSE) | MIT/X11 | 2026-11-27 |
| One Dark | [atom/one-dark-syntax](https://github.com/atom/one-dark-syntax/blob/master/LICENSE.md) | MIT | 2026-11-27 |
| One Light | [atom/one-light-syntax](https://github.com/atom/one-light-syntax/blob/master/LICENSE.md) | MIT | 2026-11-27 |
| Solarized Dark, Solarized Light | [altercation/solarized](https://github.com/altercation/solarized/blob/master/LICENSE) | MIT | 2026-11-27 |
| Rosé Pine | [rose-pine/rose-pine-theme](https://github.com/rose-pine/rose-pine-theme/blob/main/LICENSE) | MIT | 2026-11-27 |

**Aproximação, não cópia:** os temas "GitHub" mapeiam os papéis do Primer (canvas, fg, border, accent, success,
attention, danger) para os nossos. Alguns valores foram ajustados para passar no teste de contraste do catálogo. Não são
o produto GitHub nem usam marca ou ícone dele.

## Ajustes de contraste do lote 2

Os temas inspirados partem das cores OFICIAIS de cada projeto. Só a cor que não passava no teste do catálogo (texto
≥ 4,5:1, estados ≥ 4,5:1 sobre a superfície, texto do botão ≥ 4,5:1, foco ≥ 3:1) foi ajustada, misturando em direção ao
texto. Gerado por script (`data/runs/frente-b/lote2/gen.py`, fora do repo):

- nord: danger #bf616a -> #daa4a9
- nord: info #81a1c1 -> #97b1cb
- tokyo-night-day: text #3760bf -> #2b4b95
- tokyo-night-day: text_muted #6172b0 -> #515f92
- tokyo-night-day: accent_text #ffffff -> #000000
- tokyo-night-day: success #587539 -> #536e36
- tokyo-night-day: warning #8c6c3e -> #7c6037
- tokyo-night-day: danger #f52a65 -> #bf214f
- catppuccin-latte: text_muted #6c6f85 -> #606276
- catppuccin-latte: success #40a02b -> #2f7620
- catppuccin-latte: warning #df8e1d -> #915c14
- catppuccin-latte: danger #d20f39 -> #c50e36
- catppuccin-latte: info #209fb5 -> #166e7d
- gruvbox-dark: danger #fb4934 -> #fb6856
- gruvbox-light: success #79740e -> #726d0d
- gruvbox-light: warning #b57614 -> #966211
- one-light: accent_text #ffffff -> #000000
- one-light: success #50a14f -> #3a763a
- one-light: warning #c18401 -> #8d6101
- one-light: danger #e45649 -> #b24339
- one-light: info #0184bc -> #0175a6
- solarized-dark: text #93a1a1 -> #9facac
- solarized-dark: text_muted #839496 -> #91a0a2
- solarized-dark: accent_text #fdf6e3 -> #000000
- solarized-dark: success #859900 -> #93a51d
- solarized-dark: warning #b58900 -> #bd971d
- solarized-dark: danger #dc322f -> #e97a78
- solarized-dark: info #2aa198 -> #43aca4
- solarized-light: text #586e75 -> #4e6167
- solarized-light: text_muted #657b83 -> #54666d
- solarized-light: accent_text #fdf6e3 -> #000000
- solarized-light: focus #b58900 -> #aa8100
- solarized-light: success #859900 -> #5c6900
- solarized-light: warning #b58900 -> #7d5f00
- solarized-light: danger #dc322f -> #c32c29
- solarized-light: info #2aa198 -> #1d6f68
- rose-pine: info #31748f -> #5e92a8

- one-dark: accent e focus #61afef -> #c678dd (o roxo das palavras-chave do One Dark), para não ficar quase igual ao GitHub Dark Dimmed (teste de temas quase idênticos)
- catppuccin (Latte e Mocha): accent = mauve e focus = blue da própria paleta (o mauve é o destaque típico do Catppuccin), para não ficar quase igual ao Tokyo Night
- rose-pine: accent = rose #ebbcba e focus = iris #c4a7e7 (as duas da paleta), para não ficar quase igual ao Catppuccin Mocha

## Fora deste lote, com o motivo

- **Catppuccin Frappé e Macchiato:** quase idênticos entre si (fundo, superfície, texto e destaque a menos de 24 em RGB),
  e o pedido proíbe temas quase iguais para inflar a contagem. Ficam Latte (claro) e Mocha (escuro).

- **Slack:** não encontrei licença que permita reproduzir as paletas oficiais dos temas do Slack. As listas públicas
  (gists, blogs) não declaram licença, e as cores da marca seguem os termos do Slack. Isso fica pendente até haver uma
  fonte com licença. O tema "Nord" para Slack ([nordtheme/slack](https://github.com/nordtheme/slack)) é MIT; quando o
  Nord entrar, a fonte será o próprio [Nord](https://www.nordtheme.com) (MIT).
- **Darcula:** o repositório [JetBrains/intellij-community](https://github.com/JetBrains/intellij-community) responde
  `NOASSERTION` na API de licenças, e a própria JetBrains diz que parte dos arquivos de estilo não tem aviso de licença.
  Fica fora. Rosé Pine (MIT) entrou no lugar.
- **Monokai e Material:** sem licença encontrada nos repositórios conferidos (o Monokai Pro é comercial). Só entram com
  fonte licenciada.

Nenhuma dependência nova foi adicionada para o Design System: só PySide6 (já no projeto).
