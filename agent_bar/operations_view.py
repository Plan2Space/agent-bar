"""Full-mode, read-only operations view for the existing PySide6 Agent Bar."""
from __future__ import annotations

import datetime as dt
import html
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent_bar.observability import Observation
from agent_bar.runs import BenchmarkStatus, RunsView


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
        runs_box = self._box("Execuções do motor · runs/<run_id>/ (manifest, relatórios, preview)")
        runs_row = QHBoxLayout()
        runs_box.layout().addLayout(runs_row)
        self.cards["runs"] = self._label("Não coletado")
        self.cards["runs"].setOpenExternalLinks(True)
        runs_row.addWidget(self.cards["runs"], 3)
        preview_col = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(240, 160)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background:#11151c;border:1px solid #323947;color:#9aa5b5;font-size:11px")
        self.preview_caption = self._label("")
        preview_col.addWidget(self.preview)
        preview_col.addWidget(self.preview_caption)
        runs_row.addLayout(preview_col, 2)
        grid.addWidget(runs_box, 5, 0, 1, 2)
        bench_box = self._box("Benchmark Claude | Qwen | Llama")
        self.cards["benchmark"] = self._label("Não coletado")
        bench_box.layout().addWidget(self.cards["benchmark"])
        grid.addWidget(bench_box, 6, 0, 1, 2)

    @staticmethod
    def _box(title: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setStyleSheet("QGroupBox{color:#aeb5c3;background:#20242d;border:1px solid #323947;"
                          "border-radius:10px;margin-top:9px;padding-top:8px;font-size:11px;font-weight:600}"
                          "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px}")
        QVBoxLayout(box)
        return box

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setStyleSheet("color:#e0e5ef;font-size:11px;line-height:1.3")
        return label

    def update_runs(self, v: RunsView, bench: BenchmarkStatus) -> None:
        """Só o que está em disco e confere com o manifest. Sem fonte/sem dados/sem evidência ficam escritos, nunca omitidos."""
        cols = " | ".join(f"<b>{_clean(c.agent)}</b>: sem dados" if c.result is None else f"<b>{_clean(c.agent)}</b>: lido" for c in bench.columns)
        self.cards["benchmark"].setText(f"{cols}<br><span style='color:#9aa5b5'>{_clean(bench.reason)}</span>")
        first_preview = next((r for r in v.runs if r.preview_path is not None), None)
        if first_preview is None:
            self.preview.clear()
            self.preview.setText("sem imagem")
            self.preview_caption.setText("Nenhum preview com sha256 conferido")
        else:
            pix = QPixmap(str(first_preview.preview_path))
            if pix.isNull():
                self.preview.clear()
                self.preview.setText("preview ilegível")
                self.preview_caption.setText(_clean(first_preview.run_id))
            else:
                self.preview.setPixmap(pix.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
                self.preview_caption.setText(f"{_clean(first_preview.run_id)} · HEAD {_clean(first_preview.engine_commit[:12])}"
                                             f" · <a href=\"{first_preview.preview_path.as_uri()}\" style='color:#8fb4ff'>abrir</a>")
                self.preview_caption.setOpenExternalLinks(True)
        if v.state != "lido":
            tag = "SEM FONTE" if v.state == "sem_fonte" else "SEM DADOS"
            self.cards["runs"].setText(f"<b>{tag}</b><br>{_clean(v.reason)}")
            return
        rows = [f"<span style='color:#9aa5b5'>{_clean(v.reason)} · lido não comprova produção</span>"]
        for r in v.runs[:6]:
            color = "#3ddc84" if r.verdict == "PASS" and not r.problems else "#ff6b6d" if r.verdict in ("FAIL", "BLOCKED") or r.status not in ("OK", "?") else "#ffc233"
            links = " ".join(f"<a href=\"{path.as_uri()}\" style='color:#8fb4ff'>{_clean(name)}</a>" for name, path in r.report_paths.items())
            dur = "" if r.duration_s is None else f" · {r.duration_s}s"
            steps = ", ".join(f"{_clean(st.name)} {_clean(st.status)}" for st in r.steps[:7])
            rows.append(f"<b>{_clean(r.run_id)}</b> · {_clean(r.status)} · <span style='color:{color}'><b>{_clean(r.verdict or 'sem relatório')}</b></span>"
                        f" · visual {_clean(r.visual_verdict or '?')}<br>&nbsp;&nbsp;HEAD {_clean(r.engine_repo)}@{_clean(r.engine_commit[:12] or '?')}"
                        f"{dur} · {steps}<br>&nbsp;&nbsp;{links or 'nenhum arquivo conferido'}")
            if r.plan_override:
                rows.append("&nbsp;&nbsp;<span style='color:#ffc233'>plano forçado (plan_override): execução de teste do caminho de erro</span>")
            if r.failed_checks:
                rows.append("&nbsp;&nbsp;<span style='color:#ff9a9b'>checks reprovados: " + _clean("; ".join(r.failed_checks[:4])) + "</span>")
            if r.problems:
                rows.append("&nbsp;&nbsp;<span style='color:#ff9a9b'>sem evidência / problema: " + _clean("; ".join(r.problems)[:400]) + "</span>")
        self.cards["runs"].setText("<br>".join(rows))

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
