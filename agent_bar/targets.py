"""targets.py — para onde a barra olha no GitHub (org, Project e colunas). Padrao: Plan2Space Project #2. Sem Qt, sem rede.

Variaveis opcionais: AGENT_BAR_ORG, AGENT_BAR_PROJECT (numero), AGENT_BAR_COLUMNS (lista separada por virgula).
Valor invalido nunca vira consulta: cai no padrao e o motivo fica em `warning` (mostrado no painel).
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_ORG = "Plan2Space"
DEFAULT_PROJECT = 2
DEFAULT_COLUMNS = ("Backlog", "Ready", "In Progress", "Review", "Blocked", "Done")
_ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")    # regra de login do GitHub: entra numa consulta GraphQL


@dataclass(frozen=True)
class Targets:
    org: str = DEFAULT_ORG
    project: int = DEFAULT_PROJECT
    columns: tuple[str, ...] = DEFAULT_COLUMNS
    warning: str = ""

    @property
    def org_url(self) -> str:
        return f"https://github.com/{self.org}"

    @property
    def project_url(self) -> str:
        return f"https://github.com/orgs/{self.org}/projects/{self.project}"


def load_targets(env: Mapping[str, str] | None = None) -> Targets:
    e = os.environ if env is None else env
    warnings: list[str] = []
    org = (e.get("AGENT_BAR_ORG") or "").strip() or DEFAULT_ORG
    if not _ORG_RE.match(org):
        warnings.append(f"AGENT_BAR_ORG invalido ({org[:40]!r}); usando {DEFAULT_ORG}")
        org = DEFAULT_ORG
    project = DEFAULT_PROJECT
    raw = (e.get("AGENT_BAR_PROJECT") or "").strip()
    if raw:
        if raw.isdigit() and int(raw) > 0:
            project = int(raw)
        else:
            warnings.append(f"AGENT_BAR_PROJECT invalido ({raw[:20]!r}); usando {DEFAULT_PROJECT}")
    cols = tuple(c.strip() for c in (e.get("AGENT_BAR_COLUMNS") or "").split(",") if c.strip()) or DEFAULT_COLUMNS
    return Targets(org=org, project=project, columns=cols, warning="; ".join(warnings))
