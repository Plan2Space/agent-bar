"""devmode.py — modo desenvolvedor do cockpit (F6, R28–R31): autoedicao controlada do proprio Agent Bar. Sem Qt.

Fluxo: idle -> proposed -> applied_sandbox -> validated -> promoted (ou blocked em qualquer falha).
  * o agente trabalha numa COPIA (sandbox) do checkout, nunca no checkout;
  * a proposta e o conjunto de arquivos que mudaram no sandbox, so dentro da lista permitida (agent_bar/**, tests/**,
    docs/**), sem `..`, sem caminho absoluto e sem link simbolico;
  * validar = pytest + ruff no sandbox (validador injetado);
  * promover exige um token de uso unico sha256(raiz, digest, nonce, sessao), invalidado se a proposta ou a sessao
    mudarem; a promocao faz backup dos arquivos tocados e grava MANIFEST.json com hashes antes/depois.
Nada aqui faz commit, push ou deploy. `irreversible()` reconhece comandos irreversiveis para o gate de permissao.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agent_bar.cockpit.acp import CREATE_NO_WINDOW, isolated_env
from agent_bar.cockpit.store import atomic_write_json, data_dir

ALLOWED = ("agent_bar/**", "tests/**", "docs/**")
COPY_SKIP = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", "node_modules"}
STATES = ("idle", "proposed", "applied_sandbox", "validated", "promoted", "blocked")

IRREVERSIBLE = [re.compile(p, re.I) for p in (
    r"\bgit\s+push\b", r"\bgit\s+commit\b", r"\bgit\s+reset\s+--hard\b", r"\bgit\s+clean\b", r"\bgh\s+pr\b",
    r"\bgh\s+release\b", r"\bgh\s+api\b", r"\brm\s+-[a-z]*r[a-z]*f?\b", r"\bremove-item\b.*-recurse", r"\brmdir\s+/s\b",
    r"\bdel\s+/[sq]\b", r"\bdrop\s+(table|database)\b", r"\btruncate\s+table\b", r"\bterraform\b", r"\bkubectl\b",
    r"\bhelm\b", r"\bdocker\s+push\b", r"\bnpm\s+publish\b", r"\btwine\s+upload\b", r"\bdeploy\b", r"\bformat\s+[a-z]:")]


def enabled(env: dict | None = None) -> bool:
    return (os.environ if env is None else env).get("AGENT_BAR_COCKPIT_DEVMODE") == "1"


def irreversible(text: str) -> str | None:
    """O primeiro padrao irreversivel encontrado no texto (comando, titulo ou rawInput), ou None."""
    for pat in IRREVERSIBLE:
        m = pat.search(text or "")
        if m:
            return m.group(0)
    return None


def permission_text(tool_call: dict) -> str:
    """Tudo que descreve o que a ferramenta vai fazer (titulo, rawInput, conteudo), para o gate de irreversiveis."""
    parts = [str(tool_call.get("title", "")), json.dumps(tool_call.get("rawInput", ""), ensure_ascii=False)]
    for block in tool_call.get("content") or []:
        if isinstance(block, dict):
            inner = block.get("content") if block.get("type") == "content" else block
            if isinstance(inner, dict):
                parts.append(str(inner.get("text", "")))
    return "\n".join(parts)


def irreversible_request(tool_call: dict) -> str | None:
    return irreversible(permission_text(tool_call or {}))


def check_path(rel: str, allowed: Sequence[str] = ALLOWED) -> str | None:
    """Motivo da recusa de um caminho relativo da proposta, ou None se ele pode entrar."""
    if not rel or "\0" in rel:
        return "caminho vazio ou invalido"
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or re.match(r"^[A-Za-z]:", rel) or rel.startswith(("/", "\\")):
        return "caminho absoluto"
    if ".." in p.parts:
        return "caminho com '..'"
    if not any(fnmatch.fnmatchcase(p.as_posix(), g) for g in allowed):
        return f"fora da lista permitida {list(allowed)}"
    return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Change:
    path: str                      # relativo, com '/'
    before: str                    # sha256 do arquivo no checkout ("" = arquivo novo)
    after: str                     # sha256 no sandbox ("" = removido)


@dataclass
class Proposal:
    changes: list[Change]
    blocked: list[tuple[str, str]] = field(default_factory=list)      # (caminho, motivo)

    @property
    def digest(self) -> str:
        blob = "\n".join(f"{c.path}\0{c.before}\0{c.after}" for c in sorted(self.changes, key=lambda c: c.path))
        return sha256_bytes(blob.encode("utf-8"))


def _walk(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in COPY_SKIP]
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                continue
            full = Path(dirpath) / name
            out[full.relative_to(root).as_posix()] = full
    return out


def _digest_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes()) if p.is_file() else ""


def diff_trees(repo: Path, sandbox: Path, allowed: Sequence[str] = ALLOWED) -> Proposal:
    """Arquivos que mudaram no sandbox em relacao ao checkout. Mudanca fora da lista ou em link simbolico = bloqueio."""
    a, b = _walk(repo), _walk(sandbox)
    changes: list[Change] = []
    blocked: list[tuple[str, str]] = []
    for rel in sorted(set(a) | set(b)):
        before, after = (_digest_file(a[rel]) if rel in a else ""), (_digest_file(b[rel]) if rel in b else "")
        if before == after:
            continue
        if rel in b and b[rel].is_symlink():
            blocked.append((rel, "link simbolico"))
            continue
        why = check_path(rel, allowed)
        if why:
            blocked.append((rel, why))
            continue
        changes.append(Change(rel, before, after))
    return Proposal(changes, blocked)


def make_token(root: Path, digest: str, nonce: str, session: str) -> str:
    return sha256_bytes(f"{Path(root).resolve()}\0{digest}\0{nonce}\0{session}".encode("utf-8"))


ValidatorResult = tuple[bool, str]
Validator = Callable[[Path], ValidatorResult]


VALIDATOR_ALLOW = ("PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING", "VIRTUAL_ENV", "CI")


def validator_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Ambiente do validador: o mesmo isolamento do agente (lista permitida, sem credencial) + Qt offscreen.
    O codigo validado foi escrito pelo agente: nao recebe ANTHROPIC_/GH_/GITHUB_/AWS_ nem *TOKEN*/*KEY*/*SECRET*."""
    env = isolated_env(base, allow=VALIDATOR_ALLOW)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def default_validator(sandbox: Path, timeout: float = 900.0) -> ValidatorResult:
    """`pytest -q` e `ruff check .` no sandbox, com o Python deste processo e `validator_env()`. Saida truncada no fim."""
    out: list[str] = []
    ok = True
    for args in ([sys.executable, "-m", "ruff", "check", "."], [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]):
        try:
            r = subprocess.run(args, cwd=sandbox, capture_output=True, timeout=timeout, check=False,
                               env=validator_env(),
                               creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
            text = (r.stdout + r.stderr).decode("utf-8", "replace")
            out.append(f"$ {' '.join(args[2:])} -> {r.returncode}\n{text[-4000:]}")
            ok = ok and r.returncode == 0
        except subprocess.TimeoutExpired:
            out.append(f"$ {' '.join(args[2:])} -> timeout")
            ok = False
    return ok, "\n".join(out)


class DevModeError(Exception):
    pass


@dataclass
class DevSession:
    """Uma sessao do modo desenvolvedor sobre `repo`. `session_id` amarra o token a esta sessao (e a do agente)."""

    repo: Path
    root: Path = field(default_factory=lambda: data_dir() / "devmode")
    session_id: str = field(default_factory=lambda: secrets.token_hex(8))
    allowed: Sequence[str] = ALLOWED
    clock: Callable[[], float] = time.time
    state: str = "idle"
    reason: str = ""
    proposal: Proposal | None = None
    validation: str = ""
    _nonce: str = ""
    _token: str = ""
    _token_digest: str = ""
    _token_session: str = ""
    backup_dir: Path | None = None

    def __post_init__(self) -> None:
        self.repo = Path(self.repo).resolve()
        self.work = Path(self.root) / self.session_id
        self.sandbox = self.work / "sandbox"

    # ------------------------------------------------------------------------------------------------- estados
    def _block(self, why: str) -> str:
        self.state, self.reason = "blocked", why
        self._token = ""
        return self.state

    def create_sandbox(self) -> Path:
        if self.sandbox.exists():
            raise DevModeError("sandbox ja existe para esta sessao")
        shutil.copytree(self.repo, self.sandbox, ignore=shutil.ignore_patterns(*COPY_SKIP, "*.pyc"), symlinks=True)
        self.state, self.reason = "applied_sandbox", ""
        return self.sandbox

    def apply_changes(self, files: dict[str, str]) -> str:
        """Proposta vinda do agente como {caminho_relativo: conteudo}: so entra no sandbox, e so se todo caminho passar."""
        for rel in files:
            why = check_path(rel, self.allowed)
            if why:
                return self._block(f"{rel}: {why}")
        if not self.sandbox.exists():
            self.create_sandbox()
        for rel, content in files.items():
            dest = self.sandbox / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8", newline="\n")
        return self.refresh_proposal()

    def refresh_proposal(self) -> str:
        if not self.sandbox.exists():
            return self._block("sem sandbox")
        prop = diff_trees(self.repo, self.sandbox, self.allowed)
        if self.proposal is not None and prop.digest != self.proposal.digest:
            self._token = ""                                                   # proposta mudou: aprovacao anterior cai
        self.proposal = prop
        self.validation = ""
        if prop.blocked:
            return self._block("; ".join(f"{p}: {w}" for p, w in prop.blocked))
        if not prop.changes:
            self.state, self.reason = "applied_sandbox", "sandbox sem mudancas"
            return self.state
        self.state, self.reason = "proposed", ""
        return self.state

    def validate(self, validator: Validator = default_validator) -> str:
        if self.state not in ("proposed", "validated") or self.proposal is None:
            raise DevModeError(f"nada para validar (estado {self.state})")
        digest = self.proposal.digest
        ok, output = validator(self.sandbox)
        self.validation = output
        if diff_trees(self.repo, self.sandbox, self.allowed).digest != digest:
            return self._block("o sandbox mudou durante a validacao")
        if not ok:
            return self._block("validacao falhou (pytest/ruff)")
        self.state, self.reason = "validated", ""
        return self.state

    def issue_token(self, session_id: str) -> str:
        """Aprovacao de uso unico para promover ESTA proposta NESTA sessao (o Felipe confirma antes, na UI)."""
        if self.state != "validated" or self.proposal is None:
            raise DevModeError(f"so uma proposta validada recebe token (estado {self.state})")
        self._nonce = secrets.token_hex(16)
        self._token_digest = self.proposal.digest
        self._token_session = session_id
        self._token = make_token(self.repo, self._token_digest, self._nonce, session_id)
        return self._token

    def promote(self, token: str, session_id: str) -> Path:
        """Copia os arquivos validados do sandbox para o checkout, com backup + MANIFEST.json. Token consumido sempre."""
        expected, self._token = self._token, ""                               # uso unico, mesmo se falhar
        if self.state != "validated" or self.proposal is None:
            raise DevModeError(f"promocao exige proposta validada (estado {self.state})")
        if not expected or not secrets.compare_digest(token, expected):
            raise DevModeError("token invalido ou ja usado")
        if session_id != self._token_session:
            raise DevModeError("token emitido para outra sessao")
        current = diff_trees(self.repo, self.sandbox, self.allowed)
        if current.digest != self._token_digest or current.digest != self.proposal.digest:
            self._block("a proposta mudou depois da aprovacao")
            raise DevModeError("a proposta mudou depois da aprovacao")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        backup = self.work / f"backup-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        entries = []
        for c in self.proposal.changes:
            src, dst = self.sandbox / c.path, self.repo / c.path
            if _digest_file(dst) != c.before:
                self._block(f"{c.path} mudou no checkout depois da proposta")
                raise DevModeError(f"{c.path} mudou no checkout depois da proposta")
            if dst.is_file():
                (backup / c.path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / c.path)
            entries.append({"path": c.path, "before_sha256": c.before, "after_sha256": c.after})
        atomic_write_json(backup / "MANIFEST.json", {"version": 1, "repo": str(self.repo), "session": session_id,
                                                     "digest": self.proposal.digest, "created_at": self.clock(),
                                                     "files": entries})
        for c in self.proposal.changes:
            src, dst = self.sandbox / c.path, self.repo / c.path
            if c.after:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(f".{dst.name}.devmode.tmp")
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
            elif dst.exists():
                dst.unlink()
        self.state, self.reason, self.backup_dir = "promoted", "", backup
        return backup


def manifest(backup: Path) -> dict:
    return json.loads((backup / "MANIFEST.json").read_text(encoding="utf-8"))
