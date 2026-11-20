"""Full-mode, read-only operations view for the existing PySide6 Agent Bar."""
from __future__ import annotations

import datetime as dt
import html
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QTextEdit, QVBoxLayout, QWidget

from agent_bar.observability import Observation


def _time(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).astimezone().strftime("%d/%m %H:%M:%S")


def _clean(value: object) -> str:
    return html.escape(str(value or ""))


class OperationsView(QWidget):
    """Renders only observed facts; absent channels remain explicitly unavailable."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet("OperationsView{background:#17171a}")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setSpacing(8)
        self.cards: dict[str, QLabel] = {}
        cards = [
            ("timeline", "Timeline de eventos"),
            ("pipeline", "Pipeline PR / CI"),
            ("tasks", "Fila e dependências"),
            ("workers", "Workers e leases"),
            ("failures", "Retries, falhas e DLQ"),
            ("decisions", "Decisões humanas"),
            ("messages", "Conversas e mensagens"),
            ("metrics", "Produtividade · 24 h"),
        ]
        for i, (key, title) in enumerate(cards):
            box = QGroupBox(title)
            box.setStyleSheet("QGroupBox{color:#aeb5c3;background:#20242d;border:1px solid #323947;"
                              "border-radius:10px;margin-top:9px;padding-top:8px;font-size:11px;font-weight:600}"
                              "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px}")
            lay = QVBoxLayout(box)
            label = QLabel("Indisponível")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setStyleSheet("color:#e0e5ef;font-size:11px;line-height:1.3")
            lay.addWidget(label)
            self.cards[key] = label
            grid.addWidget(box, i // 2, i % 2)
        terminal_box = QGroupBox("Saída observável dos terminais · polling")
        terminal_box.setStyleSheet("QGroupBox{color:#aeb5c3;background:#171b23;border:1px solid #323947;"
                                   "border-radius:10px;margin-top:9px;padding-top:8px;font-size:11px;font-weight:600}"
                                   "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px}")
        terminal_layout = QVBoxLayout(terminal_box)
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setFixedHeight(140)
        self.terminal.setStyleSheet("background:#11151c;color:#b8d7c8;border:0;font:11px Consolas")
        terminal_layout.addWidget(self.terminal)
        grid.addWidget(terminal_box, 4, 0, 1, 2)

    def update_observation(self, o: Observation) -> None:
        if o.source not in ("lido", "demonstração"):
            self.cards["timeline"].setText(f"<b>INDISPONÍVEL</b><br>{_clean(o.reason)}")
            for key in ("pipeline", "tasks", "workers", "failures", "metrics"):
                self.cards[key].setText("Indisponível · Event Store não observado")
        else:
            timeline = [f"{_time(e.at)} · {_clean(e.event_type)} · {_clean(e.job_id)} → {_clean(e.transition)}"
                        for e in o.timeline[:8]]
            self.cards["timeline"].setText("<br>".join(timeline) if timeline else "Nenhum evento registrado")
            pipeline = [f"{_clean(e.job_id)} · {_clean(e.event_type)} · {_clean(e.transition)}"
                        for e in o.pipeline[:8]]
            self.cards["pipeline"].setText("<br>".join(pipeline) if pipeline else "Nenhum fato PR / CI registrado")
            tasks = [f"#{t.event_id} {_clean(t.job_id)} · {_clean(t.event_type)} · <b>{t.status}</b>"
                     + (f" · causa #{t.parent_event_id}" if t.parent_event_id else "")
                     for t in o.tasks[:8]]
            self.cards["tasks"].setText("<br>".join(tasks) if tasks else "Fila vazia · dependências não contratadas")
            active = [r for r in o.runs if r.ended_at is None]
            workers = [f"{_clean(r.agent)} · pid {r.pid or '?'} · início {_time(r.started_at)}"
                       for r in active]
            for task in (t for t in o.tasks if t.status == "RUNNING"):
                lease = ("vence " + _time(task.lease_until)) if task.lease_until > time.time() else "lease vencido"
                workers.append(f"{_clean(task.job_id)} · {_clean(lease)}")
            self.cards["workers"].setText(("<br>".join(workers) if workers else "Nenhum registro de subprocesso aberto")
                                          + "<br><span style='color:#9aa5b5'>Registro aberto não comprova processo vivo.</span>"
                                          + "<br><span style='color:#9aa5b5'>" + _clean(o.heartbeats_status) + "</span>")
            failed = [t for t in o.tasks if t.status == "DLQ" or t.attempt > 1]
            lines = [f"#{t.event_id} {_clean(t.job_id)} · {t.status} · tentativa {t.attempt}/{t.max_attempts}"
                     for t in failed[:8]]
            self.cards["failures"].setText(("<br>".join(lines) if lines else "Sem retries ou DLQ na fila exibida")
                                           + f"<br>Outbox pendente: {o.outbox_pending}")
            avg = "indisponível" if o.mean_run_seconds_24h is None else f"{o.mean_run_seconds_24h:.1f} s"
            self.cards["metrics"].setText(f"Eventos concluídos: {o.completed_24h}<br>DLQ: {o.failed_24h}"
                                          f"<br>Duração média de subprocesso: {avg}")
        self.cards["decisions"].setText(_clean(o.decisions_status))
        self.cards["messages"].setText(_clean(o.messages_status))
        self.terminal.setPlainText("\n".join(o.terminal) if o.terminal else o.terminal_status)
