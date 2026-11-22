"""library.py — biblioteca do cockpit (F4, R22/R23): skills, agentes e regras para injetar no prompt. So leitura. Sem Qt.

Fontes, nesta ordem:
  1. hub `claude-engineering-skills` (`skills/*/SKILL.md`), com o SHA do checkout;
  2. `.claude/` do workspace ativo (`skills/*/SKILL.md`, `agents/*.md`, `rules/*.md`);
  3. `.claude/` da raiz do workspace Plan2Space (E:\\Claude), as mesmas pastas.
Frontmatter invalido nao derruba a lista: vira aviso. O contexto injetado e montado em blocos rotulados (origem, caminho,
SHA) e truncado no total, com marcador visivel.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_bar.cockpit.acp import CREATE_NO_WINDOW

CONTEXT_LIMIT = 20_000
DEFAULT_HUB = Path(r"E:\Claude\apps\claude-engineering-skills")
DEFAULT_ROOT = Path(r"E:\Claude")
SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class LibraryItem:
    kind: str                     # skill | agent | rule
    name: str
    description: str
    path: Path
    source: str                   # "hub", "workspace", "raiz"
    sha: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.kind}:{self.path}"

    def label(self) -> str:
        origin = f"{self.source}@{self.sha[:7]}" if self.sha else self.source
        return f"[{self.kind}] {self.name} (origem: {origin}, {self.path.as_posix()})"


@dataclass
class Library:
    items: list[LibraryItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_key(self, keys: Iterable[str]) -> list[LibraryItem]:
        wanted = set(keys)
        return [i for i in self.items if i.key in wanted]


_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Frontmatter YAML minimo (`chave: valor`, com `>-`/`|` dobrados). Sem frontmatter -> ValueError."""
    m = _FM.match(text.replace("\r\n", "\n"))
    if not m:
        raise ValueError("sem frontmatter (--- ... ---)")
    meta: dict[str, str] = {}
    key = None
    buf: list[str] = []
    for line in m.group(1).split("\n"):
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv and not line.startswith((" ", "\t")):
            if key is not None:
                meta[key] = " ".join(buf).strip()
            key, val = kv.group(1), kv.group(2).strip()
            buf = [] if val in (">-", ">", "|", "|-") else [val.strip("\"'")]
        elif key is not None and line.strip():
            buf.append(line.strip())
    if key is not None:
        meta[key] = " ".join(buf).strip()
    if not meta.get("name"):
        raise ValueError("frontmatter sem `name`")
    return meta, text[m.end():]


def git_sha(repo: Path) -> str:
    if not (repo / ".git").exists():
        return ""
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, timeout=10, check=False,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = r.stdout.decode("ascii", "replace").strip()
    return out if re.fullmatch(r"[0-9a-f]{40}", out) else ""


def _scan(lib: Library, files: Iterable[Path], kind: str, source: str, sha: str) -> None:
    for f in sorted(files):
        try:
            meta, _body = parse_frontmatter(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            if kind == "rule":                                           # regras costumam nao ter frontmatter: titulo = arquivo
                try:
                    f.read_text(encoding="utf-8")
                    lib.items.append(LibraryItem(kind, f.stem, "", f, source, sha))
                    continue
                except (OSError, UnicodeDecodeError):
                    pass
            lib.warnings.append(f"{source}: {f.as_posix()}: {exc}")
            continue
        lib.items.append(LibraryItem(kind, meta["name"], meta.get("description", ""), f, source, sha))


def _scan_claude_dir(lib: Library, claude: Path, source: str, sha: str) -> None:
    if not claude.is_dir():
        return
    _scan(lib, claude.glob("skills/*/SKILL.md"), "skill", source, sha)
    _scan(lib, claude.glob("agents/*.md"), "agent", source, sha)
    _scan(lib, claude.glob("rules/*.md"), "rule", source, sha)


def discover(workspace: Path | None, hub: Path | None = None, root: Path | None = None) -> Library:
    hub = hub or Path(os.environ.get("AGENT_BAR_COCKPIT_SKILLS_HUB") or DEFAULT_HUB)
    root = root or DEFAULT_ROOT
    lib = Library()
    if hub.is_dir():
        _scan(lib, hub.glob("skills/*/SKILL.md"), "skill", "hub", git_sha(hub))
    else:
        lib.warnings.append(f"hub de skills indisponível: {hub}")
    if workspace is not None:
        _scan_claude_dir(lib, workspace / ".claude", "workspace", git_sha(workspace))
    if root is not None and (workspace is None or root.resolve() != workspace.resolve()):
        _scan_claude_dir(lib, root / ".claude", "raiz", "")
    return lib


def build_context(items: Sequence[LibraryItem], limit: int = CONTEXT_LIMIT) -> str:
    """Blocos rotulados com o conteudo integral de cada item, truncado no total em `limit` com marcador visivel."""
    if not items:
        return ""
    blocks = []
    for it in items:
        try:
            body = it.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            body = f"(ilegível: {exc})"
        blocks.append(f"### {it.label()}\n{body.strip()}")
    text = "Contexto da biblioteca (somente referência; siga as instruções do usuário abaixo):\n\n" + "\n\n".join(blocks)
    if len(text) > limit:
        omitted = len(text) - limit
        text = text[:limit] + f"\n… [contexto truncado: {omitted} caracteres omitidos]"
    return text


def compose_prompt(context: str, user_text: str) -> str:
    """O texto EXATO enviado ao agente: contexto (se houver) + separador + pedido do usuario."""
    return f"{context}{SEPARATOR}{user_text}" if context else user_text
