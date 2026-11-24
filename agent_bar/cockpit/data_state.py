"""data_state.py — de onde veio cada dado da tela e quão novo ele é (UX-05 do P2S-STUDIO-UX-CONSOLIDATION-F0). Sem Qt.

Todo estado exibido diz: a fonte (o caminho do checkout), o commit e a branch lidos, a hora da leitura e se está
AO_VIVO (lido agora), em CACHE (a última leitura falhou e o que aparece é o anterior) ou INDISPONIVEL (falhou e não há
dado anterior). Regra travada por teste: dado anterior depois de um erro nunca aparece sem "desatualizado desde".
"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

AO_VIVO, CACHE, INDISPONIVEL = "AO_VIVO", "CACHE", "INDISPONIVEL"
STATUS_TEXT = {AO_VIVO: "ao vivo", CACHE: "desatualizado", INDISPONIVEL: "indisponível"}


@dataclass(frozen=True)
class Checkout:
    branch: str = ""                 # "" = HEAD destacado ou sem git
    commit: str = ""                 # curto; "" = não é checkout git
    dirty: bool = False
    error: str = ""

    def text(self) -> str:
        if not self.commit:
            return f"sem commit ({self.error or 'não é checkout git'})"
        where = self.branch or "HEAD destacado"
        return f"{where} {self.commit}" + (" com alterações locais" if self.dirty else "")


@dataclass(frozen=True)
class DataState:
    name: str                        # evidence, motor, design-library…
    source: str                      # caminho lido
    status: str
    read_at: float | None = None     # a última leitura que DEU CERTO
    checkout: Checkout | None = None
    error: str = ""

    def line(self, fmt: Callable[[float], str] | None = None) -> str:
        hhmm = fmt or (lambda t: time.strftime("%H:%M:%S", time.localtime(t)))
        head = f"{self.name} {self.source}"
        co = f" · {self.checkout.text()}" if self.checkout is not None else ""
        if self.status == AO_VIVO:
            return f"{head}{co} · lido às {hhmm(self.read_at)} · ao vivo"
        if self.status == CACHE:
            return f"{head}{co} · desatualizado desde {hhmm(self.read_at)} (a leitura falhou: {self.error})"
        return f"{head} · indisponível: {self.error}"


def live(name: str, source: str | Path, read_at: float, checkout: Checkout | None = None) -> DataState:
    return DataState(name, str(source), AO_VIVO, read_at, checkout)


def failed(name: str, source: str | Path, error: str, previous: DataState | None) -> DataState:
    """A leitura falhou. Com leitura anterior boa: CACHE, desatualizado desde ela. Sem: INDISPONIVEL."""
    if previous is not None and previous.read_at is not None and previous.status in (AO_VIVO, CACHE):
        return replace(previous, status=CACHE, error=error)
    return DataState(name, str(source), INDISPONIVEL, None, None, error)


def _git(path: Path, *args: str, run=subprocess.run) -> str | None:
    try:
        r = run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def checkout(path: str | Path, run=subprocess.run) -> Checkout:
    """Branch, commit curto e se há alteração local no checkout lido. Só leitura (rev-parse e status)."""
    p = Path(path)
    if not p.is_dir():
        return Checkout(error="pasta não existe")
    commit = _git(p, "rev-parse", "--short=7", "HEAD", run=run)
    if not commit:
        return Checkout(error="não é checkout git")
    branch = _git(p, "rev-parse", "--abbrev-ref", "HEAD", run=run) or ""
    # --no-optional-locks: o status não regrava o índice do checkout lido (só leitura de verdade)
    status = _git(p, "--no-optional-locks", "status", "--porcelain", "--untracked-files=no", run=run)
    return Checkout("" if branch == "HEAD" else branch, commit, bool(status))


__all__ = ["AO_VIVO", "CACHE", "INDISPONIVEL", "STATUS_TEXT", "Checkout", "DataState", "checkout", "failed", "live"]
