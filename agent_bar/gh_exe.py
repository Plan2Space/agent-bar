"""gh_exe.py — UMA resolucao do executavel do GitHub CLI para o app inteiro (FR-05).

A Saude (collectors.run_gh) e a aba PRs (cockpit.github.GhClient) precisam dar o MESMO diagnostico na mesma maquina.
Antes, so o GhClient tentava o caminho padrao do instalador e o `AGENT_BAR_GH`; com o gh fora do PATH a Saude dizia
"Nao instalado" e a aba PRs "sem login". Ordem: `AGENT_BAR_GH` (explicito) > PATH > caminho padrao do instalador.
"""
from __future__ import annotations

import os
import shutil

CANDIDATES: tuple[str, ...] = ("gh", r"C:\Program Files\GitHub CLI\gh.exe")


def resolve() -> list[str]:
    """argv do gh. Sem nenhum candidato existente devolve ["gh"]: quem executa recebe o FileNotFoundError (= nao instalado)."""
    custom = os.environ.get("AGENT_BAR_GH", "").strip()
    if custom:
        return [custom]
    for cand in CANDIDATES:
        if shutil.which(cand) or os.path.isfile(cand):
            return [cand]
    return ["gh"]
