"""settings.py — preferencias da barra (posicao, transparencia, intervalos). JSON em %APPDATA%\\agent_bar (fora do repo). Sem Qt."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Settings:
    x: int | None = None
    y: int | None = None
    opacity: float = 0.88
    local_interval_s: int = 3          # coleta local (processos/logs)
    github_interval_s: int = 60        # TTL do cache do GitHub (PR/checks)
    backlog_interval_s: int = 300      # TTL do cache do Project #1 (mais caro)
    notifications: bool = False
    notify_human: bool = True          # aviso do Windows quando um PR passa a precisar do Felipe (uma vez por PR@HEAD)
    minimized: bool = False
    bar_enabled: bool = False          # D-UX-1: barra flutuante opcional e desligada; o atalho abre o Cockpit (janela principal)
    theme: str = "p2s-escuro"          # DS-01: chave do tema salvo (catálogo em cockpit/ui/theme)
    theme_favorites: list[str] = field(default_factory=list)   # TE-01: favoritos do modal de Temas


def settings_path() -> Path:
    base = os.environ.get("AGENT_BAR_SETTINGS") or os.path.join(os.environ.get("APPDATA") or str(Path.home()), "agent_bar", "settings.json")
    return Path(base)


def clamp(s: Settings) -> Settings:
    s.opacity = min(1.0, max(0.3, float(s.opacity)))
    s.local_interval_s = min(30, max(2, int(s.local_interval_s)))
    s.github_interval_s = min(600, max(15, int(s.github_interval_s)))
    s.backlog_interval_s = min(3600, max(60, int(s.backlog_interval_s)))
    return s


def load(path: Path | None = None) -> Settings:
    p = path or settings_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__} if isinstance(raw, dict) else {}
        return clamp(Settings(**known))
    except (OSError, ValueError, TypeError):
        return Settings()


def save(s: Settings, path: Path | None = None) -> None:
    p = path or settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(clamp(s)), indent=1), encoding="utf-8")
    os.replace(tmp, p)
