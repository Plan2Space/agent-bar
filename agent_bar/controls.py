"""controls.py — as poucas acoes da barra. Sem Qt. A barra so observa: nada de commit/push/merge, nada de matar processo,
nada de iniciar ou parar agentes do legado (o orquestrador FP-057/FP-060 foi removido em 2026-11-22). Abrir links do
GitHub, copiar o diagnostico e abrir o cockpit sao as acoes que restam; aprovar (S00-14) vive em approvals.py."""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from typing import Callable

from agent_bar.models import Snapshot
from agent_bar.targets import load_targets

_TARGETS = load_targets()
REPO_URL = _TARGETS.org_url                       # botao "Repositorio": a org (a fila cobre todos os repos dela)
PROJECT_URL = _TARGETS.project_url


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str


def open_url(url: str | None, opener: Callable[[str], object] | None = None) -> Result:
    if not url or not url.startswith("https://github.com/"):
        return Result(False, "URL indisponivel ou fora de github.com")
    (opener or webbrowser.open)(url)                                           # resolvido na chamada (testavel)
    return Result(True, url)


def diagnostic_text(snap: Snapshot) -> str:
    return snap.diagnostic()
