"""Generate FP-061 screenshots from an isolated, explicitly labelled fixture."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["AGENT_BAR_DEMO"] = "1"

from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar import window  # noqa: E402
from agent_bar.models import AgentStatus, Level, Snapshot  # noqa: E402
from agent_bar.observability import collect_observation  # noqa: E402
from agent_bar.settings import Settings  # noqa: E402
from tests.support.event_store import EventStore  # noqa: E402


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fp061-fixture-") as tmp:
        gov = Path(tmp)
        store = EventStore(gov / "event_store.db")
        store.publish("PR_OPENED", "visual-pr", "pr:761", {"pr": 761})
        store.publish("CI_PASSED", "visual-ci", "pr:761", {"pr": 761})
        review, _ = store.publish("REVIEW_REQUESTED", "visual-review", "pr:761", {"pr": 761})
        stdout = gov / "codex.stdout.log"
        stdout.write_text("Review fixture iniciado\nAnalisando o PR #761\nAguardando resultado observável\n", encoding="utf-8")
        store.run_started(review, "codex", ["codex", "review"], gov, 2468, stdout, gov / "codex.stderr.log")
        obs = collect_observation(gov)
        assert obs.source == "demonstração"
        app = QApplication([])
        # The offscreen Qt plugin on Windows can expose no system fonts.
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/consola.ttf")
        app.setFont(QFont("Segoe UI", 10))
        window.Worker.start = lambda self: None  # fixture visual: nenhuma coleta ou processo externo
        bar = window.Bar(Settings(), gov=gov)
        snap = Snapshot(
            at="2026-11-16T12:00:00+00:00",
            claude=AgentStatus("Claude", Level.BLUE, "Executando · fixture", "Dado demonstrativo"),
            codex=AgentStatus("Codex", Level.YELLOW, "Aguardando · fixture", "Dado demonstrativo"),
            gpt=AgentStatus("GPT", Level.GRAY, "Indisponível", "Sem contrato de estado"),
            listener=AgentStatus("Listener", Level.GREEN, "DRY_RUN · fixture", "Dado demonstrativo"),
            github=AgentStatus("GitHub", Level.GRAY, "Indisponível", "Sem chamada de rede nesta fixture"),
            mode="DRY_RUN", work="demonstração isolada", pr=761, observation=obs,
        )
        bar.show()
        bar.on_snapshot(snap)
        app.processEvents()
        assert bar.grab().save(str(out / "fp061_recolhido_demo.png"))
        bar.details.open_panel()
        app.processEvents()
        assert bar.details.grab().save(str(out / "fp061_expandido_demo.png"))
        bar.details.toggle_full_mode()
        bar.on_snapshot(snap)
        app.processEvents()
        assert bar.details.grab().save(str(out / "fp061_completo_demo.png"))
        assert bar.details.operations.grab().save(str(out / "fp061_operacoes_demo.png"))
        bar.details.close()
        bar.close()
        app.quit()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
