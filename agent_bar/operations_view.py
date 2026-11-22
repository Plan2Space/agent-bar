"""Full-mode, read-only operations view for the existing PySide6 Agent Bar."""
from __future__ import annotations

import datetime as dt
import html
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent_bar.approvals import ApprovalBoard, PrApproval, approvers_text
from agent_bar.benchmark import BenchmarkView
from agent_bar.observability import Observation
from agent_bar.runs import RunsView


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
        self.cards["benchmark"].setOpenExternalLinks(True)
        bench_box.layout().addWidget(self.cards["benchmark"])
        bench_row = QHBoxLayout()
        bench_box.layout().addLayout(bench_row)
        self.bench_columns: dict[str, QLabel] = {}
        for name in ("Claude", "Qwen", "Llama"):                                 # tres colunas lado a lado, mesma ordem sempre
            col = self._label(f"<b>{name}</b>")
            col.setOpenExternalLinks(True)
            col.setAlignment(Qt.AlignmentFlag.AlignTop)
            col.setStyleSheet("color:#e0e5ef;font-size:11px;background:#1a1e26;border:1px solid #2c3340;border-radius:8px;padding:6px")
            self.bench_columns[name] = col
            bench_row.addWidget(col, 1)
        grid.addWidget(bench_box, 6, 0, 1, 2)
        approval_box = self._box("Aprovação humana no HEAD exato (S00-14) · risco e decisão do agent-runtime")
        self.cards["approval"] = self._label("Não coletado")
        self.cards["approval"].setOpenExternalLinks(True)
        approval_box.layout().addWidget(self.cards["approval"])
        self.approve_button = QPushButton("Aprovar")
        self.approve_button.setEnabled(False)
        self.approve_button.setStyleSheet("QPushButton{background:#2b3a55;color:#e6edf7;border:1px solid #3e5a8a;border-radius:6px;padding:6px}"
                                          "QPushButton:disabled{background:#23262d;color:#6d7380;border-color:#323947}")
        self.approve_reason = self._label("")
        self.approval_picker = QComboBox()                                       # o Felipe escolhe QUAL PR o botao aprova
        self.approval_picker.setStyleSheet("QComboBox{background:#171b23;color:#e0e5ef;border:1px solid #323947;padding:4px;font-size:11px}")
        self.approval_picker.currentIndexChanged.connect(lambda _i: self._refresh_button())
        approval_box.layout().addWidget(self.approval_picker)
        approval_box.layout().addWidget(self.approve_button)
        approval_box.layout().addWidget(self.approve_reason)
        self._approval_rows: list[PrApproval] = []
        self._observe_only = False
        grid.addWidget(approval_box, 7, 0, 1, 2)

    @property
    def selected_approval(self) -> PrApproval | None:
        i = self.approval_picker.currentIndex()
        return self._approval_rows[i] if 0 <= i < len(self._approval_rows) else None

    def _refresh_button(self) -> None:
        sel = self.selected_approval
        self.approve_button.setText(f"Aprovar {sel.risk} no HEAD {sel.head_sha[:12]} · {sel.repo.split('/')[-1]}#{sel.number}"
                                    if sel is not None else "Aprovar")
        if self._observe_only:
            self.approve_button.setEnabled(False)
            self.approve_reason.setText("instância somente leitura: aprovação desativada")
        elif sel is None:
            self.approve_button.setEnabled(False)
            self.approve_reason.setText("nada para aprovar")
        else:
            self.approve_button.setEnabled(sel.can_approve)
            self.approve_reason.setText("pede confirmação e confere o HEAD e o CI de novo antes de postar; nunca faz merge"
                                        if sel.can_approve else _clean(sel.blocked_reason or sel.state))

    def _set_approval_rows(self, rows: list[PrApproval]) -> None:
        """Repopula o seletor mantendo a escolha (por repo#n); sem escolha anterior, o primeiro aprovavel."""
        previous = self.selected_approval.pr_ref if self.selected_approval is not None else None
        self._approval_rows = rows
        self.approval_picker.blockSignals(True)
        self.approval_picker.clear()
        for r in rows:
            self.approval_picker.addItem(f"{r.repo.split('/')[-1]}#{r.number} · {r.risk} · {r.state} · HEAD {r.head_sha[:12]}")
        refs = [r.pr_ref for r in rows]
        index = refs.index(previous) if previous in refs else next((i for i, r in enumerate(rows) if r.can_approve), 0 if rows else -1)
        self.approval_picker.setCurrentIndex(index)
        self.approval_picker.blockSignals(False)
        self.approval_picker.setVisible(bool(rows))

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

    _STATE = {"aguardando": ("#ffc233", "AGUARDANDO aprovação humana"), "bloqueado": ("#ff6b6d", "BLOQUEADO"),
              "aprovado": ("#3ddc84", "APROVADO no HEAD"), "erro": ("#ff6b6d", "ERRO de leitura"), "auto_low": ("#8f8f98", "LOW automático")}

    def update_approvals(self, b: ApprovalBoard, observe_only: bool = False) -> None:
        """PRs MEDIUM/HIGH com risco, HEAD, CI, evidência e aprovação vindos do runtime. Um botão, para o PR selecionado."""
        warn = f"<br><span style='color:#ffc233'>{_clean(b.credential_warning)}</span>"
        who = (f"login {_clean(b.login or '?')} · aprovadores: {_clean(approvers_text(b.approvers) if any(b.approvers.values()) else 'nenhum (AGENT_BAR_APPROVERS_MEDIUM/_HIGH)')}"
               + (f" · <span style='color:#ff9a9b'>{_clean(b.error)}</span>" if b.error else ""))
        rows = [r for r in b.rows if r.state != "auto_low"]
        if b.error and not b.rows:                                            # coletando / falhou: nunca "nenhum PR aguardando"
            self.cards["approval"].setText(f"<b>INDISPONÍVEL</b> {_clean(b.error)}{warn}")
            self._set_approval_rows([])
        elif not rows:
            low = sum(1 for r in b.rows if r.state == "auto_low")
            self.cards["approval"].setText(f"nenhum PR aguardando aprovação humana ({low} LOW automático)"
                                           f"<br><span style='color:#9aa5b5'>{who}</span>{warn}")
            self._set_approval_rows([])
        else:
            lines = []
            for r in rows:
                color, tag = self._STATE.get(r.state, ("#e0e5ef", r.state))
                link = f"<a href=\"{_clean(r.url)}\" style='color:#8fb4ff'>{_clean(r.repo.split('/')[-1])}#{r.number}</a>" \
                    if r.url.startswith("https://github.com/") else _clean(r.pr_ref)
                ev = "não se aplica" if r.evidence_ok is None else ("ok" if r.evidence_ok else "FALTA")
                lines.append(f"{link} · <b>{_clean(r.risk or '?')}</b> · <span style='color:{color}'><b>{tag}</b></span>"
                             f"<br>&nbsp;&nbsp;HEAD <code>{_clean(r.head_sha or '?')}</code> · CI {_clean(r.ci_state)} · evidência {ev}"
                             f"<br>&nbsp;&nbsp;<span style='color:#9aa5b5'>{_clean(r.evidence_note)}</span>"
                             f"<br>&nbsp;&nbsp;<span style='color:#9aa5b5'>risco: {_clean('; '.join(r.risk_reasons[:3]))}</span>"
                             + (f"<br>&nbsp;&nbsp;<span style='color:{'#3ddc84' if r.state == 'aprovado' else '#ff9a9b'}'>"   # aprovado nao e bloqueio
                                f"{_clean(r.blocked_reason)}</span>" if r.blocked_reason else "")
                             + (f"<br>&nbsp;&nbsp;aprovação: {_clean(r.approval.approver)} · <a href=\"{_clean(r.approval.source)}\" "
                                f"style='color:#8fb4ff'>registro</a>" if r.approval else ""))
            self.cards["approval"].setText("<br>".join(lines) + f"<br><span style='color:#9aa5b5'>{who}</span>{warn}")
            self._set_approval_rows([r for r in rows if r.state != "aprovado"])
        self._observe_only = observe_only
        self._refresh_button()

    def update_benchmark(self, v: BenchmarkView) -> None:
        """Benchmark do runtime: cabecalho com fonte e commit de origem; uma coluna por backend. Falha = INDISPONIVEL, sem numero."""
        origin = f"commit de origem {_clean(v.origin_commit[:12])}" if v.origin_commit else "commit de origem não informado (AGENT_BAR_BENCHMARK_COMMIT)"
        if v.state != "lido":
            tag = "SEM FONTE" if v.state == "sem_fonte" else "INDISPONÍVEL"
            self.cards["benchmark"].setText(f"<b>{tag}</b> {_clean(v.reason)}")
            for name, col in self.bench_columns.items():
                col.setText(f"<b>{name}</b><br>sem dados")
            return
        md = f" · <a href=\"{v.comparison_md.as_uri()}\" style='color:#8fb4ff'>comparison.md</a>" if v.comparison_md else " · comparison.md ausente"
        self.cards["benchmark"].setText(f"<span style='color:#9aa5b5'>{_clean(v.reason)} · {origin}{md} · lido não comprova produção</span>")
        rec_color = {"autonomo_controlado": "#3ddc84", "assistente_supervisionado": "#4aa8ff", "so_tarefas_simples": "#ffc233", "inadequado": "#ff6b6d"}

        def val(x, unit=""):
            return "?" if x is None else f"{x:g}{unit}" if isinstance(x, float) else f"{x}{unit}"
        for b in v.backends:
            col = self.bench_columns.get(b.title)
            if col is None:
                continue
            if b.success is None:
                col.setText(f"<b>{_clean(b.title)}</b><br>sem dados<br><span style='color:#ff9a9b'>{_clean('; '.join(b.problems))}</span>")
                continue
            links = " ".join(f"<a href=\"{r.path.as_uri()}\" style='color:#8fb4ff'>{_clean(r.scenario.split('_')[0])}r{r.repeat or '?'}</a>"
                             for r in b.results)
            rec = b.recommendation or "?"
            col.setText(f"<b>{_clean(b.title)}</b> <span style='color:#9aa5b5'>{_clean(b.model or '?')}</span>"
                        f"<br>sucesso <b>{val(b.success)}/{val(b.runs)}</b> · visíveis {val(b.visible_pass)}/{val(b.runs)} · ocultos {val(b.hidden_pass)}/{val(b.runs)}"
                        f"<br>tentativas (mediana) {val(b.median_attempts)} · tempo {val(b.median_duration_s, ' s')}"
                        f"<br>ferramentas {val(b.median_tool_calls)} · erro de ferramenta {val(b.tool_error_rate)}"
                        f"<br>tokens {val(b.median_prompt_tokens)}/{val(b.median_completion_tokens)} · GPU {val(b.max_gpu_mem_mib, ' MiB')} (sistema)"
                        f"<br>falha de infra {val(b.infra_failures)} · violação de contrato {val(b.contract_violations)}"
                        f"<br>recomendação <b style='color:{rec_color.get(rec, '#e0e5ef')}'>{_clean(rec)}</b>"
                        f"<br>result.json: {links or 'nenhum'}"
                        + (f"<br><span style='color:#ff9a9b'>{_clean('; '.join(b.problems)[:300])}</span>" if b.problems else ""))

    def update_runs(self, v: RunsView) -> None:
        """Só o que está em disco e confere com o manifest. Sem fonte/sem dados/sem evidência ficam escritos, nunca omitidos."""
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
            if r.continuity is not None:
                c = r.continuity
                color = "#3ddc84" if c.continuous else "#ff9a9b"
                rows.append(f"&nbsp;&nbsp;<span style='color:{color}'>continuidade: {c.solids} sólido{'s' if c.solids != 1 else ''} · "
                            f"{c.seams} emenda(s) · {c.internal_faces} face(s) interna(s)"
                            + (f" · {c.non_manifold} não-manifold" if c.non_manifold else "")
                            + (f" · {c.overlaps} sobreposição(ões)" if c.overlaps else "")
                            + f"</span> <span style='color:#9aa5b5'>(C4 v{_clean(r.geometry_version)})</span>")
            elif r.geometry_version and r.geometry_version.startswith("1."):
                rows.append(f"&nbsp;&nbsp;<span style='color:#9aa5b5'>continuidade não medida (C4 v{_clean(r.geometry_version)})</span>")
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
