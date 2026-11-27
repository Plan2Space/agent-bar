"""DS-01 (P2S-STUDIO-DESIGN-SYSTEM-001; pedido do Felipe de 2026-11-27, §5.2 e §5.4): tokens semânticos versionados.

O catálogo só entra se for legível: texto e texto secundário ≥ 4,5:1 sobre o fundo e sobre a superfície (≥ 7:1 nos temas
de alto contraste), estados (sucesso/aviso/erro/info) ≥ 4,5:1 sobre a superfície (são usados como texto), texto do botão
principal ≥ 4,5:1, anel de foco ≥ 3:1 sobre o fundo. Estado nunca só por cor: cada um tem uma palavra.
Tema importado é DADO: campo desconhecido, cor fora de #rrggbb e número fora da faixa são recusados.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_bar.cockpit.ui.theme import catalog as C
from agent_bar.cockpit.ui.theme import tokens as T

ORIGINALS = ("Studio Sage", "Industrial Black", "Walnut Boutique", "Bronze Luxury", "Concrete", "Architectural White",
             "Warm Studio", "Graphite")


def test_the_first_batch_has_github_high_contrast_and_the_eight_originals():
    names = {t.name for t in C.THEMES}
    assert {"GitHub Light", "GitHub Dark", "GitHub Dark Dimmed"} <= names
    assert any(t.high_contrast and t.dark for t in C.THEMES) and any(t.high_contrast and not t.dark for t in C.THEMES)
    assert set(ORIGINALS) <= names
    assert len({t.key for t in C.THEMES}) == len(C.THEMES)                     # chaves únicas


def test_the_default_is_todays_look():
    d = C.get(C.DEFAULT)
    assert (d.bg, d.text, d.border) == ("#16161a", "#e6e6e6", "#3a3a44")


@pytest.mark.parametrize("theme", C.THEMES, ids=lambda t: t.key)
def test_every_theme_is_readable(theme):
    need = 7.0 if theme.high_contrast else 4.5
    for fg in ("text", "text_muted"):
        for bg in ("bg", "surface"):
            got = T.contrast(theme.color(fg), theme.color(bg))
            assert got >= need, f"{theme.key}: {fg} sobre {bg} = {got:.2f}"
    for state in ("success", "warning", "danger", "info"):
        got = T.contrast(theme.color(state), theme.surface)
        assert got >= 4.5, f"{theme.key}: {state} sobre surface = {got:.2f}"
    assert T.contrast(theme.accent_text, theme.accent) >= 4.5, theme.key
    assert T.contrast(theme.text, theme.selection) >= 4.5, theme.key
    assert T.contrast(theme.focus, theme.bg) >= 3.0, theme.key


@pytest.mark.parametrize("theme", C.THEMES, ids=lambda t: t.key)
def test_every_theme_has_its_source_recorded(theme):
    assert theme.source in (C.PRIMER, C.P2S)


def test_states_never_rely_on_color_alone():
    assert set(T.STATUS_WORD) == {"success", "warning", "danger", "info"}
    assert all(word.strip() for word in T.STATUS_WORD.values())


def test_a_theme_round_trips_through_json():
    t = C.get("graphite")
    assert T.from_dict(json.loads(json.dumps(t.to_dict()))) == t


@pytest.mark.parametrize("bad, why", [
    ({"schema": "p2s-theme/9"}, "versão"),
    ({"exec": "import os"}, "campo desconhecido"),
    ({"text": "red"}, "cor inválida"),
    ({"text": "#12345"}, "cor inválida"),
    ({"font_size": 99}, "font_size"),
    ({"density": "gigante"}, "density"),
    ({"key": "../../x"}, "key"),
    ({"radius": "5; drop"}, "radius"),
])
def test_an_imported_theme_is_only_data(bad, why):
    data = C.get("graphite").to_dict() | bad
    with pytest.raises(T.ThemeError, match=why):
        T.from_dict(data)


def test_search_and_category_filter():
    assert [t.key for t in C.find("dimmed")] == ["github-dark-dimmed"]
    assert all(t.category == "GitHub" for t in C.find(category="GitHub")) and len(C.find(category="GitHub")) == 3


def test_mix_and_spacing():
    assert T.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert replace(C.get("graphite"), density="compacta").spacing() < C.get("graphite").spacing()
