"""tokens.py — os tokens semânticos do Design System do P2S Studio (P2S-STUDIO-DESIGN-SYSTEM-001, pedido do Felipe de
2026-11-27, §5.4). Sem Qt: dados, validação e contraste.

Um `Theme` guarda só PAPÉIS ("fundo", "texto secundário", "perigo"), nunca "o cinza do card do Chat". Os widgets pedem o
papel; trocar o tema troca o valor. O formato é versionado (`SCHEMA`) para o editor/importação de temas (§5.3): um JSON de
tema é DADO (cores, fonte, tamanho, densidade, raio) e nunca código, e `from_dict` recusa o que não reconhece.

Acessibilidade: `contrast()` é a razão do WCAG 2.x; o teste do catálogo exige texto ≥ 4,5:1 (≥ 7:1 nos temas de alto
contraste) e estado nunca só por cor (`STATUS_WORD`).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace

SCHEMA = "p2s-theme/1"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT = re.compile(r"^[A-Za-z0-9 ._-]{1,60}$")                                # nome de fonte: nada que vire sintaxe de QSS
COLOR_ROLES = ("bg", "surface", "surface_alt", "border", "text", "text_muted", "accent", "accent_text", "selection",
               "focus", "success", "warning", "danger", "info")
DENSITIES = ("compacta", "normal", "confortável")
STATUS_WORD = {"success": "OK", "warning": "Atenção", "danger": "Erro", "info": "Info"}   # estado nunca só por cor


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    category: str                      # "Plan2Space", "GitHub", "Alto contraste", ...
    dark: bool
    bg: str                            # fundo da janela
    surface: str                       # cards, campos, tabelas
    surface_alt: str                   # cabeçalhos de tabela, hover, área secundária
    border: str
    text: str
    text_muted: str
    accent: str                        # ação principal, link, seleção ativa
    accent_text: str                   # texto sobre o accent
    selection: str                     # fundo de item selecionado
    focus: str                         # anel de foco (teclado)
    success: str
    warning: str
    danger: str
    info: str
    font_family: str = "Segoe UI"
    font_size: int = 9                 # pt
    density: str = "normal"
    radius: int = 5                    # px
    high_contrast: bool = False
    source: str = ""                   # de onde veio a paleta (licença registrada em SOURCES.md)

    def __post_init__(self) -> None:
        """Defesa na origem (revisão do #157, MUST): todo Theme, venha do catálogo, do editor ou de JSON importado, só
        carrega VALORES. Nada que chega ao QSS pode trazer sintaxe: a fonte segue uma allowlist estrita (sem aspas,
        `;`, chaves, `\\`, `@` nem `url(`), as cores são #rrggbb e os números ficam na faixa."""
        if not isinstance(self.font_family, str) or not FONT.match(self.font_family):
            raise ThemeError("font_family inválida: só letras, números, espaço, ponto, hífen e sublinhado (até 60)")
        for role in COLOR_ROLES:
            value = getattr(self, role)
            if not isinstance(value, str) or not HEX.match(value):
                raise ThemeError(f"cor inválida em {role!r}: use #rrggbb")
        if not isinstance(self.font_size, int) or isinstance(self.font_size, bool) or not 7 <= self.font_size <= 20:
            raise ThemeError("font_size: inteiro entre 7 e 20")
        if self.density not in DENSITIES:
            raise ThemeError(f"density: uma de {', '.join(DENSITIES)}")
        if not isinstance(self.radius, int) or isinstance(self.radius, bool) or not 0 <= self.radius <= 16:
            raise ThemeError("radius: inteiro entre 0 e 16")

    def color(self, role: str) -> str:
        if role not in COLOR_ROLES:
            raise KeyError(f"papel de cor desconhecido: {role!r}")
        return getattr(self, role)

    def spacing(self) -> int:
        """Espaçamento base em px conforme a densidade."""
        return {"compacta": 4, "normal": 6, "confortável": 9}[self.density]

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, **asdict(self)}


class ThemeError(ValueError):
    """Tema inválido (importação, editor): a mensagem diz o campo."""


_FIELDS = {f.name: f for f in fields(Theme)}


def from_dict(data: object) -> Theme:
    """Valida um tema vindo de JSON. Só dados: campo desconhecido, cor fora de #rrggbb, número fora da faixa = recusa."""
    if not isinstance(data, dict):
        raise ThemeError("o tema precisa ser um objeto JSON")
    if data.get("schema") != SCHEMA:
        raise ThemeError(f"versão do tema não suportada: {data.get('schema')!r} (esperado {SCHEMA})")
    body = {k: v for k, v in data.items() if k != "schema"}
    unknown = sorted(set(body) - set(_FIELDS))
    if unknown:
        raise ThemeError(f"campo desconhecido: {', '.join(unknown)}")
    for role in COLOR_ROLES:
        value = body.get(role)
        if not isinstance(value, str) or not HEX.match(value):
            raise ThemeError(f"cor inválida em {role!r}: use #rrggbb")
    if not isinstance(body.get("font_family", "Segoe UI"), str) or not FONT.match(body.get("font_family", "Segoe UI")):
        raise ThemeError("font_family inválida: só letras, números, espaço, ponto, hífen e sublinhado (até 60)")
    for name in ("key", "name", "category", "source"):
        if name in body and (not isinstance(body[name], str) or len(body[name]) > 120):
            raise ThemeError(f"texto inválido em {name!r}")
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,60}$", str(body.get("key", ""))):
        raise ThemeError("key: só letras minúsculas, números e hífen")
    if not isinstance(body.get("font_size", 9), int) or not 7 <= body.get("font_size", 9) <= 20:
        raise ThemeError("font_size: inteiro entre 7 e 20")
    if body.get("density", "normal") not in DENSITIES:
        raise ThemeError(f"density: uma de {', '.join(DENSITIES)}")
    if not isinstance(body.get("radius", 5), int) or not 0 <= body.get("radius", 5) <= 16:
        raise ThemeError("radius: inteiro entre 0 e 16")
    for flag in ("dark", "high_contrast"):
        if flag in body and not isinstance(body[flag], bool):
            raise ThemeError(f"{flag}: verdadeiro ou falso")
    return Theme(**body)


# ------------------------------------------------------------------------------------------------ contraste (WCAG 2.x)
def _channel(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: str, b: str, t: float) -> str:
    """Cor entre a e b (t=0 → a, t=1 → b). Para hover/sombra derivados sem novo token."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def with_font(theme: Theme, family: str | None = None, size: int | None = None, density: str | None = None) -> Theme:
    return replace(theme, font_family=family or theme.font_family, font_size=size or theme.font_size,
                   density=density or theme.density)


__all__ = ["COLOR_ROLES", "DENSITIES", "SCHEMA", "STATUS_WORD", "Theme", "ThemeError", "contrast", "from_dict",
           "luminance", "mix", "with_font"]
