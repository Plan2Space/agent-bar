"""agent_registry.py — processos de agente que o Studio iniciou, para achar orfaos depois de uma queda (F5 no app).

No Windows o agente roda num Job Object com KILL_ON_JOB_CLOSE: se o Studio cai, o sistema encerra o agente junto
(medido: Studio morto a forca -> agente morto em ~3 s). Este registro cobre o que sobra: Job que falhou ao criar ou
ao atribuir (`AcpClient.contained = False`), ou outro caminho que escape do Job.

O arquivo guarda o PID e o instante de criacao do proprio Studio e de cada agente. Na abertura seguinte, se o Studio
que gravou ja nao existe, os agentes cuja identidade (PID + criacao) ainda bate sao orfaos daquela execucao. So esses
podem ser encerrados; um PID reaproveitado por outro programa nunca bate.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from agent_bar.platform.proc import creation_time, kill_tree, same_process

log = logging.getLogger("agent_bar.cockpit.agent_registry")
SCHEMA = "p2s.agent-registry.v1"


class AgentRegistry:
    def __init__(self, path: Path, killer=kill_tree):
        self.path = path
        self.killer = killer
        self._last: list[tuple[int, str]] | None = None

    def previous_orphans(self) -> list[dict]:
        """Agentes de uma execucao anterior que caiu e que ainda estao vivos (identidade conferida)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return []
        studio = data.get("studio") or {}
        if studio.get("pid") == os.getpid() or same_process(int(studio.get("pid") or 0), studio.get("created")):
            return []                                                        # quem gravou ainda roda: nao e orfao
        return [p for p in data.get("agents") or []
                if isinstance(p, dict) and isinstance(p.get("pid"), int) and same_process(p["pid"], p.get("created"))]

    def record(self, agents: list[tuple[int, str]]) -> None:
        """Grava (pid, perfil) dos agentes vivos desta execucao; so escreve quando o conjunto muda."""
        agents = sorted(agents)
        if agents == self._last:
            return
        self._last = agents
        data = {"schema": SCHEMA, "studio": {"pid": os.getpid(), "created": creation_time(os.getpid())},
                "agents": [{"pid": pid, "created": creation_time(pid), "profile": profile} for pid, profile in agents]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.warning("registro de agentes nao gravado")

    def end(self, orphans: list[dict]) -> list[int]:
        """Encerra so os que ainda batem (PID + criacao). Devolve os PIDs encerrados."""
        ended = []
        for p in orphans:
            if same_process(p["pid"], p.get("created")) and self.killer(p["pid"]):
                ended.append(p["pid"])
                log.info("agente orfao encerrado pid=%s perfil=%s", p["pid"], p.get("profile"))
        return ended
