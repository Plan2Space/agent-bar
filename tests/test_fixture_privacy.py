"""Guarda de privacidade: o agent-bar é PÚBLICO e nada copiado de fonte privada da org entra em fixture ou doc.

Origem: o fixture do Project #1 do #156 copiou 12 cards reais do board privado (títulos, URLs, campos). Regras:

1. Em ``tests/fixtures/``: nenhuma referência a issue/PR/arquivo/commit de repo privado (URL nem ``repo#N``).
2. Fixture no formato do ``gh project item-list`` (``items`` com ``content``): todo card é sintético. O título leva o
   marcador "sintétic" e o repositório não é um repo real da org (o Project #1 também é privado).
3. Em ``docs/``: repo privado só por link imutável a arquivo (``blob``/``tree`` num SHA de 40), que é como o contrato
   de ownership manda citar SPEC; issue e PR de repo privado não entram.

Visibilidade conferida com ``gh repo view --json visibility`` em 2026-11-27. Repo novo privado: acrescente aqui.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORG = "Plan2Space"
PRIVATE_REPOS = ("claude-engineering-skills", "design-library", "engineering-specs", "evidence", "sketchup-mcp-next")
PUBLIC_REPOS = ("agent-bar", "agent-runtime", "sketchup-mcp")
_P = "|".join(re.escape(r) for r in PRIVATE_REPOS)
# referência a CONTEÚDO de repo privado: URL de issue/PR/arquivo/commit, "Org/repo#123" ou "repo#123" sem a org.
# Sem diferenciar maiúsculas: o GitHub não diferencia na org nem no repo.
_PRIVATE_CONTENT = re.compile(rf"github\.com/{ORG}/(?:{_P})/(?:issues|pull|blob|tree|commit|raw)/"
                              rf"|{ORG}/(?:{_P})#\d+|(?<![\w./-])(?:{_P})#\d+", re.IGNORECASE)
# em docs só é aceito link imutável a arquivo: blob|tree/<sha40>/
_DOC_ALLOWED = re.compile(rf"github\.com/{ORG}/(?:{_P})/(?:blob|tree)/[0-9a-f]{{40}}/", re.IGNORECASE)
_PRIVATE_NAMES = {r.lower() for r in PRIVATE_REPOS} | {f"{ORG}/{r}".lower() for r in PRIVATE_REPOS}
_SYNTHETIC = "sintétic"
_REAL_REPOS = tuple(f"{ORG}/{r}" for r in PRIVATE_REPOS + PUBLIC_REPOS)


def fixture_violations(path: Path, text: str) -> list[str]:
    out = [f"{path}: conteúdo de repo privado: {m.group(0)}" for m in _PRIVATE_CONTENT.finditer(text)]
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return out
        for obj in _objects(data):         # lista de issues fora do formato do Project: título + repo privado
            repo = str(obj.get("repository") or obj.get("repo") or "").strip().lower()
            if obj.get("title") and repo in _PRIVATE_NAMES:
                out.append(f"{path}: item com título de repo privado {repo}: {str(obj['title'])[:60]!r}")
        items = data.get("items") if isinstance(data, dict) else None
        for i, item in enumerate(items or []):
            if not isinstance(item, dict) or not isinstance(item.get("content"), dict):
                continue
            titles = [str(item.get("title") or ""), str(item["content"].get("title") or "")]
            if not all(_SYNTHETIC in t for t in titles if t):
                out.append(f"{path}: card {i} do Project não é sintético: {titles[0][:60]!r}")
            repos = {str(item.get("repository") or ""), str(item["content"].get("repository") or "")}
            if repos & set(_REAL_REPOS):
                out.append(f"{path}: card {i} aponta para repo real da org: {sorted(repos & set(_REAL_REPOS))}")
    return out


def _objects(data: object):
    if isinstance(data, dict):
        yield data
        for v in data.values():
            yield from _objects(v)
    elif isinstance(data, list):
        for v in data:
            yield from _objects(v)


def doc_violations(path: Path, text: str) -> list[str]:
    return [f"{path}: issue/PR ou link mutável de repo privado: {m.group(0)}"
            for m in _PRIVATE_CONTENT.finditer(text)
            if not _DOC_ALLOWED.match(text, m.start(0))]


def _files(base: Path, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix in suffixes)


def test_fixtures_carry_nothing_copied_from_private_sources():
    bad = []
    for p in _files(ROOT / "tests" / "fixtures", (".json", ".jsonl", ".txt", ".md", ".yml", ".yaml", ".csv")):
        bad += fixture_violations(p.relative_to(ROOT), p.read_text(encoding="utf-8", errors="replace"))
    assert bad == []


def test_docs_reference_private_repos_only_by_immutable_file_link():
    bad = []
    for p in _files(ROOT / "docs", (".md",)):
        bad += doc_violations(p.relative_to(ROOT), p.read_text(encoding="utf-8", errors="replace"))
    assert bad == []


# ----------------------------------------------------------------- a guarda reprova o que vazou (casos sintéticos)


def _card(title: str, repo: str, url: str) -> dict:
    return {"title": title, "repository": repo, "status": "Ready",
            "content": {"title": title, "repository": repo, "number": 29, "type": "Issue", "url": url}}


@pytest.mark.parametrize("card", [
    _card("[Feature] Um card copiado do board", f"{ORG}/claude-engineering-skills",
          f"https://github.com/{ORG}/claude-engineering-skills/issues/29"),
    _card("[Epic] Título real de issue pública", f"{ORG}/sketchup-mcp", f"https://github.com/{ORG}/sketchup-mcp/issues/409"),
    _card("Tarefa sintética 01", f"{ORG}/engineering-specs", "https://github.com/Plan2Space/exemplo/issues/1"),
])
def test_guard_rejects_a_project_card_copied_from_the_board(card):
    text = json.dumps({"items": [card]})
    assert fixture_violations(Path("tests/fixtures/x.json"), text)


def test_guard_accepts_a_synthetic_card():
    card = _card("Tarefa sintética 01", f"{ORG}/exemplo", f"https://github.com/{ORG}/exemplo/issues/1001")
    assert fixture_violations(Path("tests/fixtures/x.json"), json.dumps({"items": [card]})) == []


@pytest.mark.parametrize("text,bad", [
    (f"ver {ORG}/evidence#12", True),
    (f"[x](https://github.com/{ORG}/design-library/pull/3)", True),
    (f"[x](https://github.com/{ORG}/engineering-specs/blob/develop/a.md)", True),
    (f"[x](https://github.com/{ORG}/engineering-specs/blob/{'a' * 40}/a.md)", False),
    (f"[x](https://github.com/{ORG}/agent-runtime/issues/50)", False),
])
def test_doc_guard(text, bad):
    assert bool(doc_violations(Path("docs/x.md"), text)) is bad


# ------------------------------------------------- follow-up do #219 (SHOULDs do revisor): brechas de escopo


@pytest.mark.parametrize("text", [
    "corrigido em sketchup-mcp-next#76",                                   # repo#N sem a org
    "ver plan2space/evidence#12",                                          # org em minúsculas
    "https://github.com/plan2space/design-library/issues/3",               # URL com a org em minúsculas
])
def test_fixture_guard_rejects_private_refs_without_org_or_in_lowercase(text):
    assert fixture_violations(Path("tests/fixtures/x.txt"), text)


def test_fixture_guard_rejects_a_bare_issue_list_from_a_private_repo():
    text = json.dumps([{"title": "Uma issue real", "repository": "plan2space/claude-engineering-skills"}])
    assert fixture_violations(Path("tests/fixtures/x.json"), text)


def test_fixture_guard_rejects_a_real_title_even_with_a_synthetic_repo_and_url():
    """Isola a regra do marcador: repo inventado e URL sintética não bastam, o título precisa ser sintético."""
    card = _card("[Feature] Título copiado do board", f"{ORG}/exemplo", f"https://github.com/{ORG}/exemplo/issues/1001")
    assert fixture_violations(Path("tests/fixtures/x.json"), json.dumps({"items": [card]}))


@pytest.mark.parametrize("text,bad", [
    ("ver sketchup-mcp-next#76", True),
    ("ver PLAN2SPACE/engineering-specs#9", True),
    ("ver agent-runtime#50", False),                                       # repo público
    ("o next-gen-sketchup-mcp-next#1 não é da org", False),                # nome maior que contém o privado
])
def test_doc_guard_without_org_and_any_case(text, bad):
    assert bool(doc_violations(Path("docs/x.md"), text)) is bad
