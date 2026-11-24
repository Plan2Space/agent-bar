"""github_tabs.py — abas PRs e Actions do cockpit (F3). So leitura: o `gh` roda numa thread, a UI so recebe o resultado."""
from __future__ import annotations

import html
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar import controls as ctl
from agent_bar.cockpit.data_state import AO_VIVO, CACHE, DataState, failed, live
from agent_bar.cockpit.github import GhClient, GhError, checks_summary, review_prompt
from agent_bar.cockpit.profiles import DEFAULT_APPS
from agent_bar.targets import load_targets

COMBO_CSS = "QComboBox,QLineEdit{background:#26262c;color:#e6e6e6;border:1px solid #3a3a44;border-radius:5px;padding:2px 6px}"
BTN_CSS = ("QPushButton{background:#2a2a30;color:#e6e6e6;border:1px solid #3a3a44;border-radius:5px;padding:4px 10px}"
           "QPushButton:hover{background:#34343c}QPushButton:disabled{color:#666}")
TREE_CSS = ("QTreeWidget{background:#1a1a1f;color:#dcdce2;border:1px solid #2e2e36;font-size:12px}"
            "QTreeWidget::item:selected{background:#2c3a55}QHeaderView::section{background:#22222a;color:#aaa;border:0;padding:3px}")
TEXT_CSS = "background:#121215;color:#d6d6dc;border:1px solid #2e2e36;font-size:12px"
DIFF_UNAVAILABLE = "(diff indisponível"                   # prefixo do texto quando o `gh pr diff` falha (UX-09)
ALL = "*"                                              # "todos (N)" no seletor de repo (UX-09)
REVIEW_COLOR = {"APPROVED": "#3ddc84", "CHANGES_REQUESTED": "#ff6b6d", "REVIEW_REQUIRED": "#ffc233"}


class _Bridge(QObject):
    done = Signal(int, str, object, object)            # pedido, etiqueta, resultado, erro


class _GhTab(QWidget):
    """Base: executor de 1 thread, contador de pedidos (resposta velha e ignorada) e linha de estado."""

    def __init__(self, gh: GhClient | None, executor: ThreadPoolExecutor | None) -> None:
        super().__init__()
        self.gh = gh or GhClient()
        self.pool = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="cockpit-gh")
        self._own_pool = executor is None
        self.req = 0
        self.bridge = _Bridge()
        self.bridge.done.connect(self._done)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#8f8f98;font-size:11px")
        self.status.setWordWrap(True)

    def submit(self, tag: str, fn: Callable[[], object]) -> Future:
        self.req += 1
        rid = self.req
        self.status.setText("consultando o GitHub…")
        fut = self.pool.submit(fn)
        fut.add_done_callback(lambda f, r=rid, t=tag: self.bridge.done.emit(r, t, None if f.exception() else f.result(),
                                                                            f.exception()))
        return fut

    def _done(self, rid: int, tag: str, result: object, err: object) -> None:
        if rid != self.req and tag in ("list",):
            return
        if err is not None:
            kind = err.kind if isinstance(err, GhError) else "failed"
            msg = err.message if isinstance(err, GhError) else str(err)
            self.status.setText(f"<span style='color:#ff9a9b'>[{kind}] {html.escape(msg)}</span>")
            self.status.setTextFormat(Qt.TextFormat.RichText)
            self.on_error(tag, kind, msg)
            return
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.on_result(tag, result)

    def on_error(self, tag: str, kind: str, msg: str) -> None:
        """Gancho: a falha já está na linha de estado; a subclasse decide o que fazer com o que está na tela."""

    def on_result(self, tag: str, result: object) -> None:                    # pragma: no cover - sobrescrito
        raise NotImplementedError

    def shutdown(self) -> None:
        if self._own_pool:
            self.pool.shutdown(wait=True, cancel_futures=True)


def _repo_box(repos: list[str]) -> QComboBox:
    box = QComboBox()
    box.setStyleSheet(COMBO_CSS)
    for r in repos:
        box.addItem(r, r)
    return box


class PrsTab(_GhTab):
    """PRs abertos (UX-09): "todos (N)" por padrão, CI do PR dentro do PR e a hora da leitura (DataState do UX-05).

    Regra travada por teste: erro do `gh` com a tabela anterior do MESMO recorte → a tabela fica e o cabeçalho diz
    "desatualizado desde"; sem leitura anterior desse recorte → a tabela é limpa e diz "indisponível". Repo que falhou
    dentro de "todos" aparece "não coletado", nunca como 0 PRs."""

    review_requested = Signal(str, str)                 # workspace, prompt

    def __init__(self, gh: GhClient | None = None, executor: ThreadPoolExecutor | None = None,
                 repos: list[str] | None = None, apps_root: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(gh, executor)
        self.apps_root = apps_root or DEFAULT_APPS
        self.clock = clock
        self.all_repos = list(repos or [f"{load_targets().org}/agent-bar"])
        self.items: list[dict] = []
        self.current: dict | None = None
        self.current_repo = ""
        self.current_diff = ""
        self.data_state: DataState | None = None
        self.shown_key = ""                              # recorte cuja tabela está na tela
        self._pending_key = ""
        self.missing: dict[str, str] = {}                # repo -> motivo (não coletado na última leitura)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.repo_box = QComboBox()
        self.repo_box.setStyleSheet(COMBO_CSS)
        if len(self.all_repos) > 1:
            self.repo_box.addItem(f"todos ({len(self.all_repos)})", ALL)
        for r in self.all_repos:
            self.repo_box.addItem(r, r)
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.setStyleSheet(BTN_CSS)
        cap = QLabel("Repositório")
        cap.setStyleSheet("color:#8f8f98;font-size:11px")
        head.addWidget(cap)
        head.addWidget(self.repo_box, 1)
        head.addWidget(self.refresh_btn)
        ro = QLabel("somente leitura: comentar, aprovar e mergear ficam no GitHub")
        ro.setStyleSheet("color:#6d6d75;font-size:11px")
        ro.setWordWrap(True)
        head.addWidget(ro, 1)
        lay.addLayout(head)
        self.fresh = QLabel("ainda não lido")                                   # fonte · hora da leitura · estado
        self.fresh.setWordWrap(True)
        self.fresh.setTextFormat(Qt.TextFormat.RichText)
        self.fresh.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.fresh)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setStyleSheet(TREE_CSS)
        self.tree.setHeaderLabels(["#", "Repo", "Título", "Autor", "Revisão", "Checks", "Merge", "Branch"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 50)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 340)
        split.addWidget(self.tree)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 4, 0, 0)
        actions = QHBoxLayout()
        self.open_btn = QPushButton("Abrir no GitHub")
        self.review_btn = QPushButton("Revisão assistida (abre no Chat)")
        for b in (self.open_btn, self.review_btn):
            b.setStyleSheet(BTN_CSS)
            b.setEnabled(False)
            actions.addWidget(b)
        actions.addStretch(1)
        bl.addLayout(actions)
        self.detail = QTextBrowser()
        self.detail.setStyleSheet(TEXT_CSS)
        self.detail.setOpenExternalLinks(False)
        self.detail.setOpenLinks(False)                                          # link do run abre no navegador
        self.detail.anchorClicked.connect(lambda url: ctl.open_url(url.toString()))
        bl.addWidget(self.detail, 1)
        split.addWidget(bottom)
        split.setSizes([260, 380])
        lay.addWidget(split, 1)
        lay.addWidget(self.status)
        self.refresh_btn.clicked.connect(self.refresh)
        self.repo_box.activated.connect(lambda _i: self.refresh())
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._select(cur))
        self.open_btn.clicked.connect(lambda: ctl.open_url((self.current or {}).get("url")))
        self.review_btn.clicked.connect(self.request_review)

    def repo(self) -> str:
        """O repo do PR selecionado; sem seleção, o do recorte (vazio em "todos")."""
        key = str(self.repo_box.currentData() or "")
        return self.current_repo or ("" if key == ALL else key)

    def key(self) -> str:
        return str(self.repo_box.currentData() or "")

    def selected_repos(self) -> list[str]:
        key = self.key()
        return list(self.all_repos) if key == ALL else [key]

    def refresh(self) -> None:
        self._pending_key = self.key()
        repos = self.selected_repos()
        self.submit("list", lambda: self._collect(repos))

    def _collect(self, repos: list[str]) -> tuple[list[tuple[str, dict]], dict[str, str]]:
        """Lê cada repo; falha de um repo vira "não coletado". Se TODOS falharem, a leitura falhou (erro tipado)."""
        rows: list[tuple[str, dict]] = []
        missing: dict[str, str] = {}
        first: GhError | None = None
        for repo in repos:
            try:
                rows += [(repo, pr) for pr in self.gh.prs(repo)]
            except GhError as exc:
                missing[repo] = exc.message
                first = first or exc
        if first is not None and len(missing) == len(repos):
            raise first
        return rows, missing

    def _label(self, key: str) -> str:
        return f"todos ({len(self.all_repos)})" if key == ALL else key

    def _render_fresh(self) -> None:
        st = self.data_state
        if st is None:
            self.fresh.setText("ainda não lido")
            return
        color = {AO_VIVO: "#8f8f98", CACHE: "#ffb347"}.get(st.status, "#ff9a9b")
        parts = [f"<span style='color:{color}'>{html.escape(st.line())}</span>"]
        if st.status == AO_VIVO:
            n = len(self.items)
            parts.append(f"{n} PR(s) aberto(s)" + (" nos repos coletados" if self.missing else ""))
        if self.missing:
            gone = "; ".join(f"{r.split('/')[-1]}: não coletado ({m[:120]})" for r, m in sorted(self.missing.items()))
            parts.append(f"<span style='color:#ffb347'>{html.escape(gone)}</span>")
        self.fresh.setText(" · ".join(parts))

    def _select(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        number = int(item.data(0, Qt.ItemDataRole.UserRole))
        repo = str(item.data(1, Qt.ItemDataRole.UserRole) or "")
        self.review_btn.setEnabled(False)
        self._pending_detail = (repo, number)
        self.submit("detail", lambda: self._load_detail(repo, number))

    def _load_detail(self, repo: str, number: int) -> tuple[str, dict, str, tuple[list[dict], str]]:
        pr = self.gh.pr(repo, number)                                          # GraphQL: sem o PR, nada a mostrar
        try:                                                                    # REST: a cota pode acabar sozinha
            diff = self.gh.pr_diff(repo, number)
        except GhError as exc:
            diff = f"{DIFF_UNAVAILABLE}: {exc.message})"
        return repo, pr, diff, self._pr_ci(repo, pr)

    def _pr_ci(self, repo: str, pr: dict) -> tuple[list[dict], str]:
        """As execuções do Actions do HEAD exato do PR (só leitura). Falha aqui não derruba o detalhe: vira o motivo."""
        head, branch = str(pr.get("headRefOid") or ""), str(pr.get("headRefName") or "")
        try:
            runs = self.gh.runs(repo, branch=branch, limit=20) if branch else []
        except GhError as exc:
            return [], exc.message
        return [r for r in runs if str(r.get("headSha") or "") == head], ""

    def on_result(self, tag: str, result: object) -> None:
        if tag == "list":
            rows, missing = result  # type: ignore[misc]
            self.items = [pr for _repo, pr in rows]
            self.missing = dict(missing)
            self.shown_key = self._pending_key
            self.current, self.current_repo = None, ""
            self.data_state = live("PRs", self._label(self.shown_key), self.clock())
            self.tree.clear()
            for repo, pr in rows:
                rd = str(pr.get("reviewDecision") or "")
                it = QTreeWidgetItem([str(pr.get("number")), repo.split("/")[-1],
                                      ("[rascunho] " if pr.get("isDraft") else "") + str(pr.get("title", "")),
                                      str((pr.get("author") or {}).get("login", "")), rd.lower().replace("_", " ") or "—",
                                      checks_summary(pr.get("statusCheckRollup")), str(pr.get("mergeable", "")).lower(),
                                      f"{pr.get('headRefName', '')} → {pr.get('baseRefName', '')}"])
                it.setData(0, Qt.ItemDataRole.UserRole, int(pr.get("number") or 0))
                it.setData(1, Qt.ItemDataRole.UserRole, repo)
                self.tree.addTopLevelItem(it)
            self._render_fresh()
            self.status.setText("")
        elif tag == "detail":
            repo, pr, diff, ci = result  # type: ignore[misc]
            self.current, self.current_repo, self.current_diff = pr, repo, diff
            self.open_btn.setEnabled(bool(pr.get("url")))
            no_diff = diff.startswith(DIFF_UNAVAILABLE)                       # revisão sem diff não revisa nada
            self.review_btn.setEnabled(not no_diff)
            self.review_btn.setToolTip("sem o diff do PR, a revisão assistida fica indisponível" if no_diff else "")
            self.detail.setHtml(self._detail_html(pr, diff, ci))
            self.status.setText(f"{repo}#{pr.get('number')} · HEAD {pr.get('headRefOid', '?')}")

    def on_error(self, tag: str, kind: str, msg: str) -> None:
        if tag == "detail":                                                    # #107: o PR anterior não fica na tela
            repo, number = getattr(self, "_pending_detail", ("", 0))
            self.current, self.current_repo, self.current_diff = None, "", ""
            self.open_btn.setEnabled(False)
            self.review_btn.setEnabled(False)
            self.detail.setHtml(f"<h3 style='color:#fff'>{html.escape(repo)}#{number}</h3>"
                                f"<p style='color:#ff9a9b'>detalhe indisponível: [{html.escape(kind)}] "
                                f"{html.escape(msg)}</p>")
            return
        if tag != "list":
            return
        key = self._pending_key
        if self.data_state is not None and self.shown_key == key:              # a tabela na tela é deste recorte
            self.data_state = failed("PRs", self._label(key), msg, self.data_state)
        else:                                                                   # nunca mostrar a de outro recorte
            self.tree.clear()
            self.items, self.missing, self.shown_key = [], {}, key
            self.current, self.current_repo = None, ""
            self.detail.clear()
            self.open_btn.setEnabled(False)
            self.review_btn.setEnabled(False)
            self.data_state = failed("PRs", self._label(key), msg, None)
        self._render_fresh()

    @staticmethod
    def _ci_html(pr: dict, ci: tuple[list[dict], str] | None) -> str:
        e = html.escape
        sha = str(pr.get("headRefOid") or "")[:7]
        head = f"<h4>CI deste PR (Actions do HEAD {e(sha)})</h4>"
        if ci is None:
            return head + "<p>—</p>"
        runs, err = ci
        if err:
            return head + f"<p style='color:#ff9a9b'>CI indisponível: {e(err)}</p>"
        if not runs:
            return head + f"<p>nenhuma execução do Actions para o HEAD {e(sha)}</p>"
        rows = []
        for r in runs:
            state = str(r.get("conclusion") or r.get("status") or "")
            color = {"success": "#3ddc84", "failure": "#ff6b6d", "cancelled": "#8a8f98"}.get(state, "#ffc233")
            rows.append(f"<li>{e(str(r.get('workflowName', '')))} · <b style='color:{color}'>{e(state)}</b> · "
                        f"{e(str(r.get('event', '')))} · {e(str(r.get('createdAt', '')))} · "
                        f"<a href='{e(str(r.get('url', '')))}'>abrir</a></li>")
        return head + f"<ul>{''.join(rows)}</ul>"

    @classmethod
    def _detail_html(cls, pr: dict, diff: str, ci: tuple[list[dict], str] | None = None) -> str:
        e = html.escape
        rd = str(pr.get("reviewDecision") or "")
        checks = "".join(f"<li>{e(str(c.get('name') or c.get('context') or '?'))}: "
                         f"{e(str(c.get('conclusion') or c.get('state') or c.get('status') or ''))}</li>"
                         for c in pr.get("statusCheckRollup") or [] if isinstance(c, dict))
        comments = "".join(f"<p><b>{e(str((c.get('author') or {}).get('login', '')))}</b>: {e(str(c.get('body', ''))[:600])}</p>"
                           for c in (pr.get("comments") or [])[-5:] if isinstance(c, dict))
        return (f"<h3 style='color:#fff'>#{pr.get('number')} {e(str(pr.get('title', '')))}</h3>"
                f"<p>HEAD <code>{e(str(pr.get('headRefOid', '')))}</code> · {e(str(pr.get('headRefName', '')))} → "
                f"{e(str(pr.get('baseRefName', '')))} · revisão <b style='color:{REVIEW_COLOR.get(rd, '#ccc')}'>{e(rd or '—')}</b>"
                f" · merge {e(str(pr.get('mergeable', '')))}</p>"
                + cls._ci_html(pr, ci) +
                f"<h4>Checks</h4><ul>{checks or '<li>sem checks</li>'}</ul>"
                f"<pre style='white-space:pre-wrap'>{e(str(pr.get('body') or '')[:4000])}</pre>"
                f"<h4>Últimos comentários</h4>{comments or '<p>—</p>'}"
                f"<h4>Diff</h4><pre style='font-family:Consolas,monospace;font-size:11px'>{e(diff[:20000])}</pre>")

    def local_checkout(self) -> Path | None:
        name = self.repo().split("/")[-1]
        if not name:
            return None
        p = self.apps_root / name
        return p.resolve() if (p / ".git").exists() else None

    def request_review(self) -> None:
        if not self.current:
            return
        ws = self.local_checkout()
        if ws is None:
            self.status.setText(f"sem checkout local de {self.repo()} em {self.apps_root}: revisão assistida indisponível")
            return
        self.review_requested.emit(str(ws), review_prompt(self.repo(), self.current, self.current_diff))


class ActionsTab(_GhTab):
    def __init__(self, gh: GhClient | None = None, executor: ThreadPoolExecutor | None = None,
                 repos: list[str] | None = None) -> None:
        super().__init__(gh, executor)
        self.runs: list[dict] = []
        self.log_text = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.repo_box = _repo_box(repos or [f"{load_targets().org}/agent-bar"])
        self.branch = QLineEdit()
        self.branch.setPlaceholderText("branch (opcional)")
        self.branch.setStyleSheet(COMBO_CSS)
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.setStyleSheet(BTN_CSS)
        self.auto = QCheckBox("auto (60 s)")
        self.auto.setStyleSheet("color:#bdbdc4")
        for w, s in ((self.repo_box, 2), (self.branch, 1), (self.refresh_btn, 0), (self.auto, 0)):
            head.addWidget(w, s)
        lay.addLayout(head)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setStyleSheet(TREE_CSS)
        self.tree.setHeaderLabels(["Workflow", "Título", "Branch", "Estado", "Evento", "SHA", "Criado"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(1, 300)
        split.addWidget(self.tree)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 4, 0, 0)
        tools = QHBoxLayout()
        self.failed_only = QCheckBox("só jobs com falha")
        self.failed_only.setStyleSheet("color:#bdbdc4")
        self.search = QLineEdit()
        self.search.setPlaceholderText("filtrar linhas do log…")
        self.search.setStyleSheet(COMBO_CSS)
        self.open_btn = QPushButton("Abrir no GitHub")
        self.open_btn.setStyleSheet(BTN_CSS)
        self.open_btn.setEnabled(False)
        for w in (self.failed_only, self.search, self.open_btn):
            tools.addWidget(w)
        bl.addLayout(tools)
        self.jobs = QLabel("")
        self.jobs.setTextFormat(Qt.TextFormat.RichText)
        self.jobs.setWordWrap(True)
        self.jobs.setStyleSheet("color:#d6d6dc;font-size:12px")
        bl.addWidget(self.jobs)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("QPlainTextEdit{" + TEXT_CSS + ";font-family:Consolas,monospace;font-size:11px}")
        bl.addWidget(self.log, 1)
        split.addWidget(bottom)
        split.setSizes([260, 380])
        lay.addWidget(split, 1)
        lay.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.refresh)
        self.auto.toggled.connect(lambda on: self.timer.start() if on else self.timer.stop())
        self.refresh_btn.clicked.connect(self.refresh)
        self.repo_box.activated.connect(lambda _i: self.refresh())
        self.branch.returnPressed.connect(self.refresh)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._select(cur))
        self.search.textChanged.connect(lambda _t: self._render_log())
        self.open_btn.clicked.connect(self._open)

    def repo(self) -> str:
        return str(self.repo_box.currentData() or "")

    def refresh(self) -> None:
        repo, branch = self.repo(), self.branch.text().strip()
        self.submit("list", lambda: self.gh.runs(repo, branch))

    def _select(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        run_id = int(item.data(0, Qt.ItemDataRole.UserRole))
        repo, failed = self.repo(), self.failed_only.isChecked()
        self.open_btn.setEnabled(True)
        self.submit("detail", lambda: (self.gh.run_jobs(repo, run_id), self._safe_log(repo, run_id, failed)))

    def _safe_log(self, repo: str, run_id: int, failed: bool) -> str:
        try:
            return self.gh.run_log(repo, run_id, failed_only=failed)
        except GhError as exc:                                                 # run em andamento ainda nao tem log
            return f"(log indisponível: {exc.message})"

    def _open(self) -> None:
        it = self.tree.currentItem()
        if it is not None:
            ctl.open_url(str(it.data(1, Qt.ItemDataRole.UserRole) or ""))

    def on_result(self, tag: str, result: object) -> None:
        if tag == "list":
            self.runs = list(result or [])  # type: ignore[arg-type]
            self.tree.clear()
            for r in self.runs:
                state = str(r.get("conclusion") or r.get("status") or "")
                it = QTreeWidgetItem([str(r.get("workflowName", "")), str(r.get("displayTitle", "")), str(r.get("headBranch", "")),
                                      state, str(r.get("event", "")), str(r.get("headSha", ""))[:10], str(r.get("createdAt", ""))])
                it.setData(0, Qt.ItemDataRole.UserRole, int(r.get("databaseId") or 0))
                it.setData(1, Qt.ItemDataRole.UserRole, str(r.get("url", "")))
                color = {"success": "#3ddc84", "failure": "#ff6b6d", "cancelled": "#8a8f98"}.get(state, "#ffc233")
                it.setForeground(3, _brush(color))
                self.tree.addTopLevelItem(it)
            self.status.setText(f"{len(self.runs)} execução(ões) em {self.repo()}")
        elif tag == "detail":
            jobs, log = result  # type: ignore[misc]
            rows = []
            for j in jobs:
                concl = str(j.get("conclusion") or j.get("status") or "")
                rows.append(f"<b>{html.escape(str(j.get('name', '')))}</b> "
                            f"<span style='color:{'#3ddc84' if concl == 'success' else '#ff6b6d' if concl == 'failure' else '#ffc233'}'>"
                            f"{html.escape(concl)}</span>")
            self.jobs.setText(" · ".join(rows) or "sem jobs")
            self.log_text = log
            self._render_log()
            self.status.setText(f"{len(jobs)} job(s) · log com {len(log.splitlines())} linha(s)")

    def _render_log(self) -> None:
        q = self.search.text().strip().lower()
        lines = self.log_text.splitlines()
        if q:
            lines = [ln for ln in lines if q in ln.lower()]
        self.log.setPlainText("\n".join(lines[-5000:]))


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(color))
