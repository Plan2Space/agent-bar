"""catalog.py — o catálogo de temas (primeiro lote do §5.2 do pedido de 2026-11-27).

- **P2S Escuro (atual):** o visual de hoje, como padrão. Trocar de Design System não muda nada até o usuário escolher.
- **GitHub Light, Dark, Dark Dimmed:** inspirados nas paletas do Primer (`primer/primitives`, MIT © GitHub). Ver
  SOURCES.md. São aproximações dos papéis semânticos do Primer, não uma cópia do produto.
- **Alto contraste:** escuro e claro, texto ≥ 7:1.
- **Plan2Space (originais):** Studio Sage, Industrial Black, Walnut Boutique, Bronze Luxury, Concrete, Architectural White,
  Warm Studio, Graphite.
- **Inspirados (lote 2):** Dracula, Nord, Tokyo Night (Night, Storm, Day), Catppuccin (Latte, Mocha),
  Gruvbox (Dark, Light), One Dark, One Light, Solarized (Dark, Light), Rosé Pine. Licenças MIT conferidas no repositório
  de cada um (SOURCES.md). Frappé e Macchiato ficaram fora por serem quase idênticos entre si (teste de
  duplicatas). Darcula ficou fora: o repositório da JetBrains não declara licença (NOASSERTION).

Temas do Slack ficaram de fora deste lote: não achei licença que permita reproduzir as paletas (SOURCES.md).
Cada tema passa no teste de contraste do catálogo. Não há temas quase idênticos para inflar a contagem.
"""
from __future__ import annotations

from agent_bar.cockpit.ui.theme.tokens import Theme

PRIMER = "primer/primitives (MIT © GitHub)"
P2S = "original Plan2Space"

THEMES: tuple[Theme, ...] = (
    Theme("p2s-escuro", "P2S Escuro (atual)", "Plan2Space", True,
          bg="#16161a", surface="#1e1e23", surface_alt="#26262c", border="#3a3a44", text="#e6e6e6",
          text_muted="#a3a3ad", accent="#4aa8ff", accent_text="#0b0b0d", selection="#2c3a55", focus="#4aa8ff",
          success="#3ddc84", warning="#ffc233", danger="#ff8587", info="#7cc0ff", source=P2S),
    # ------------------------------------------------------------------------------------------ GitHub (Primer, MIT)
    Theme("github-light", "GitHub Light", "GitHub", False,
          bg="#ffffff", surface="#f6f8fa", surface_alt="#eaeef2", border="#d0d7de", text="#1f2328",
          text_muted="#59636e", accent="#0969da", accent_text="#ffffff", selection="#ddf4ff", focus="#0969da",
          success="#1a7f37", warning="#9a6700", danger="#d1242f", info="#0969da", source=PRIMER),
    Theme("github-dark", "GitHub Dark", "GitHub", True,
          bg="#0d1117", surface="#151b23", surface_alt="#212830", border="#3d444d", text="#f0f6fc",
          text_muted="#9198a1", accent="#4493f8", accent_text="#0d1117", selection="#1f3a5f", focus="#1f6feb",
          success="#3fb950", warning="#d29922", danger="#f85149", info="#4493f8", source=PRIMER),
    Theme("github-dark-dimmed", "GitHub Dark Dimmed", "GitHub", True,
          bg="#212830", surface="#262c36", surface_alt="#2a313c", border="#3d444d", text="#d1d7e0",
          text_muted="#9ba5b2", accent="#6cb6ff", accent_text="#212830", selection="#2d4566", focus="#4184e4",
          success="#57ab5a", warning="#c69026", danger="#f47067", info="#6cb6ff", source=PRIMER),
    # ------------------------------------------------------------------------------------------ alto contraste
    Theme("alto-contraste-escuro", "Alto contraste escuro", "Alto contraste", True,
          bg="#000000", surface="#0a0a0a", surface_alt="#1a1a1a", border="#ffffff", text="#ffffff",
          text_muted="#e6e6e6", accent="#ffd60a", accent_text="#000000", selection="#003a75", focus="#ffd60a",
          success="#5cff7d", warning="#ffd60a", danger="#ff8c8c", info="#8fd3ff", high_contrast=True, source=P2S),
    Theme("alto-contraste-claro", "Alto contraste claro", "Alto contraste", False,
          bg="#ffffff", surface="#ffffff", surface_alt="#f0f0f0", border="#000000", text="#000000",
          text_muted="#1a1a1a", accent="#0000c8", accent_text="#ffffff", selection="#cfe3ff", focus="#0000c8",
          success="#005c14", warning="#5c3d00", danger="#a30000", info="#00439c", high_contrast=True, source=P2S),
    # ------------------------------------------------------------------------------------------ Plan2Space
    Theme("studio-sage", "Studio Sage", "Plan2Space", False,
          bg="#f2f5f0", surface="#ffffff", surface_alt="#e6ece2", border="#c3cfbd", text="#1d281d",
          text_muted="#4a584a", accent="#3d6a4d", accent_text="#ffffff", selection="#d6e6d6", focus="#3d6a4d",
          success="#2a6e38", warning="#7f5300", danger="#ad2a22", info="#29598a", source=P2S),
    Theme("industrial-black", "Industrial Black", "Plan2Space", True,
          bg="#0e0e0f", surface="#18181a", surface_alt="#222225", border="#3a3a3f", text="#ececec",
          text_muted="#a7a7ad", accent="#f2a33a", accent_text="#0e0e0f", selection="#3b2c14", focus="#f2a33a",
          success="#4cc38a", warning="#f2c14e", danger="#ff7b70", info="#6cb6ff", source=P2S),
    Theme("walnut-boutique", "Walnut Boutique", "Plan2Space", True,
          bg="#1e1612", surface="#291f19", surface_alt="#33271f", border="#53412f", text="#f2e8dc",
          text_muted="#c4b09a", accent="#d29a5f", accent_text="#1e1612", selection="#4a3423", focus="#e0ad73",
          success="#8ccf86", warning="#e8b55a", danger="#f08a7a", info="#97c1e6", source=P2S),
    Theme("bronze-luxury", "Bronze Luxury", "Plan2Space", True,
          bg="#0b0b0c", surface="#161616", surface_alt="#222120", border="#46402f", text="#efe6d8",
          text_muted="#bcae95", accent="#a8875a", accent_text="#0b0b0c", selection="#3a3122", focus="#c4a06c",
          success="#6fcf97", warning="#e9c46a", danger="#f4807a", info="#86b9ea", source=P2S),
    Theme("concrete", "Concrete", "Plan2Space", False,
          bg="#e8e8e6", surface="#f5f5f3", surface_alt="#dcdcd9", border="#b9b9b4", text="#1f1f1f",
          text_muted="#4d4d4a", accent="#3f5163", accent_text="#ffffff", selection="#cfd6dd", focus="#3f5163",
          success="#256c2a", warning="#7a4f00", danger="#a8241c", info="#26557d", source=P2S),
    Theme("architectural-white", "Architectural White", "Plan2Space", False,
          bg="#ffffff", surface="#fafafa", surface_alt="#f0f0f0", border="#d4d4d4", text="#111111",
          text_muted="#555555", accent="#0050a8", accent_text="#ffffff", selection="#dbe8f7", focus="#0050a8",
          success="#1c7430", warning="#7f5300", danger="#b8261f", info="#0050a8", source=P2S),
    Theme("warm-studio", "Warm Studio", "Plan2Space", False,
          bg="#faf6f0", surface="#fffdf9", surface_alt="#f1e9dd", border="#dcccb6", text="#2b2118",
          text_muted="#654f3b", accent="#a84b25", accent_text="#ffffff", selection="#f2dccb", focus="#a84b25",
          success="#2c6e38", warning="#7f5300", danger="#ab2a20", info="#2a5986", source=P2S),
    Theme("graphite", "Graphite", "Plan2Space", True,
          bg="#1f2124", surface="#282b2f", surface_alt="#31353a", border="#454a51", text="#e4e6e8",
          text_muted="#a8aeb5", accent="#8ab4f8", accent_text="#1f2124", selection="#34465f", focus="#8ab4f8",
          success="#81c995", warning="#fdd663", danger="#f28b82", info="#8ab4f8", source=P2S),
    # ------------------------------------------------------------------------------------------ lote 2: inspirados
    # paletas oficiais de cada projeto (licença MIT conferida, ver SOURCES.md); só o que não passava no contraste foi
    # ajustado (mistura em direção ao texto), e o ajuste está registrado no SOURCES.md
    Theme("dracula", "Dracula", "Inspirados", True,
          bg="#282a36", surface="#21222c", surface_alt="#343746", border="#44475a", text="#f8f8f2", text_muted="#9ea8c7", accent="#bd93f9", accent_text="#282a36", selection="#44475a", focus="#ff79c6", success="#50fa7b", warning="#f1fa8c", danger="#ff5555", info="#8be9fd", source="dracula/dracula-theme (MIT)"),
    Theme("nord", "Nord", "Inspirados", True,
          bg="#2e3440", surface="#3b4252", surface_alt="#434c5e", border="#4c566a", text="#eceff4", text_muted="#d8dee9", accent="#88c0d0", accent_text="#2e3440", selection="#4c566a", focus="#88c0d0", success="#a3be8c", warning="#ebcb8b", danger="#daa4a9", info="#97b1cb", source="nordtheme/nord (MIT)"),
    Theme("tokyo-night", "Tokyo Night", "Inspirados", True,
          bg="#1a1b26", surface="#16161e", surface_alt="#292e42", border="#3b4261", text="#c0caf5", text_muted="#a9b1d6", accent="#7aa2f7", accent_text="#1a1b26", selection="#33467c", focus="#7aa2f7", success="#9ece6a", warning="#e0af68", danger="#f7768e", info="#7dcfff", source="tokyo-night/tokyo-night-vscode-theme (MIT)"),
    Theme("tokyo-night-storm", "Tokyo Night Storm", "Inspirados", True,
          bg="#24283b", surface="#1f2335", surface_alt="#292e42", border="#3b4261", text="#c0caf5", text_muted="#a9b1d6", accent="#7aa2f7", accent_text="#1f2335", selection="#2e3c64", focus="#7aa2f7", success="#9ece6a", warning="#e0af68", danger="#f7768e", info="#7dcfff", source="tokyo-night/tokyo-night-vscode-theme (MIT)"),
    Theme("tokyo-night-day", "Tokyo Night Day", "Inspirados", False,
          bg="#e1e2e7", surface="#e9e9ed", surface_alt="#d0d5e3", border="#c4c8da", text="#2b4b95", text_muted="#515f92", accent="#2e7de9", accent_text="#000000", selection="#b7c1e3", focus="#2e7de9", success="#536e36", warning="#7c6037", danger="#bf214f", info="#007197", source="tokyo-night/tokyo-night-vscode-theme (MIT)"),
    Theme("catppuccin-latte", "Catppuccin Latte", "Inspirados", False,
          bg="#eff1f5", surface="#e6e9ef", surface_alt="#dce0e8", border="#bcc0cc", text="#4c4f69", text_muted="#606276", accent="#8839ef", accent_text="#ffffff", selection="#ccd0da", focus="#1e66f5", success="#2f7620", warning="#915c14", danger="#c50e36", info="#166e7d", source="catppuccin/catppuccin (MIT)"),
    Theme("catppuccin-mocha", "Catppuccin Mocha", "Inspirados", True,
          bg="#1e1e2e", surface="#181825", surface_alt="#313244", border="#45475a", text="#cdd6f4", text_muted="#a6adc8", accent="#cba6f7", accent_text="#11111b", selection="#45475a", focus="#89b4fa", success="#a6e3a1", warning="#f9e2af", danger="#f38ba8", info="#89dceb", source="catppuccin/catppuccin (MIT)"),
    Theme("gruvbox-dark", "Gruvbox Dark", "Inspirados", True,
          bg="#282828", surface="#32302f", surface_alt="#3c3836", border="#504945", text="#ebdbb2", text_muted="#bdae93", accent="#fabd2f", accent_text="#282828", selection="#504945", focus="#fe8019", success="#b8bb26", warning="#fabd2f", danger="#fb6856", info="#83a598", source="morhetz/gruvbox (MIT/X11, no README)"),
    Theme("gruvbox-light", "Gruvbox Light", "Inspirados", False,
          bg="#fbf1c7", surface="#f9f5d7", surface_alt="#ebdbb2", border="#d5c4a1", text="#3c3836", text_muted="#665c54", accent="#076678", accent_text="#fbf1c7", selection="#d5c4a1", focus="#af3a03", success="#726d0d", warning="#966211", danger="#9d0006", info="#076678", source="morhetz/gruvbox (MIT/X11, no README)"),
    Theme("one-dark", "One Dark", "Inspirados", True,
          bg="#282c34", surface="#21252b", surface_alt="#2c313a", border="#3e4451", text="#dcdfe4", text_muted="#abb2bf", accent="#c678dd", accent_text="#21252b", selection="#3e4451", focus="#c678dd", success="#98c379", warning="#e5c07b", danger="#e06c75", info="#56b6c2", source="atom/one-dark-syntax (MIT)"),
    Theme("one-light", "One Light", "Inspirados", False,
          bg="#fafafa", surface="#f0f0f1", surface_alt="#e5e5e6", border="#d4d4d6", text="#383a42", text_muted="#696c77", accent="#4078f2", accent_text="#000000", selection="#e5e5e6", focus="#4078f2", success="#3a763a", warning="#8d6101", danger="#b24339", info="#0175a6", source="atom/one-light-syntax (MIT)"),
    Theme("solarized-dark", "Solarized Dark", "Inspirados", True,
          bg="#002b36", surface="#073642", surface_alt="#0e4250", border="#586e75", text="#9facac", text_muted="#91a0a2", accent="#268bd2", accent_text="#000000", selection="#0e4250", focus="#b58900", success="#93a51d", warning="#bd971d", danger="#e97a78", info="#43aca4", source="altercation/solarized (MIT)"),
    Theme("solarized-light", "Solarized Light", "Inspirados", False,
          bg="#fdf6e3", surface="#eee8d5", surface_alt="#e6dfc8", border="#93a1a1", text="#4e6167", text_muted="#54666d", accent="#268bd2", accent_text="#000000", selection="#e6dfc8", focus="#aa8100", success="#5c6900", warning="#7d5f00", danger="#c32c29", info="#1d6f68", source="altercation/solarized (MIT)"),
    Theme("rose-pine", "Rosé Pine", "Inspirados", True,
          bg="#191724", surface="#1f1d2e", surface_alt="#26233a", border="#403d52", text="#e0def4", text_muted="#908caa", accent="#ebbcba", accent_text="#191724", selection="#403d52", focus="#c4a7e7", success="#9ccfd8", warning="#f6c177", danger="#eb6f92", info="#5e92a8", source="rose-pine/rose-pine-theme (MIT)"),
)

DEFAULT = "p2s-escuro"
CATEGORIES = tuple(dict.fromkeys(t.category for t in THEMES))


def get(key: str) -> Theme:
    for t in THEMES:
        if t.key == key:
            return t
    raise KeyError(f"tema desconhecido: {key!r}")


def find(query: str = "", category: str = "") -> list[Theme]:
    q = query.strip().lower()
    return [t for t in THEMES if (not category or t.category == category)
            and (not q or q in t.name.lower() or q in t.category.lower())]


__all__ = ["CATEGORIES", "DEFAULT", "THEMES", "find", "get"]
