"""controls.py — operacoes seguras da barra (FP-058). Sem Qt. A barra NAO e um segundo orquestrador: nada de commit/push/branch,
nada de matar processo por nome, nada de iniciar Claude, nenhum modo de execucao. Parar/reiniciar usa a Tarefa Agendada (mecanismo existente);
o `--watch-pid` do executor (#594) encerra a cadeia python quando o launcher morre."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_bar import collectors as col
from agent_bar.models import Snapshot

REPO_URL = "https://github.com/GFCDOTA/sketchup-mcp"
PROJECT_URL = "https://github.com/orgs/GFCDOTA/projects/1"


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str


def run_ps(script: str, timeout: float = 30.0) -> tuple[int, str]:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, creationflags=col.NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def active_execution(procs: list[col.Proc] | None) -> str | None:
    """Motivo para NAO parar (execucao ativa ou estado nao observavel), ou None se parar e seguro."""
    if procs is None:
        return "tabela de processos indisponivel: nao da para provar que nao ha execucao ativa"
    roots = {p.pid for p in col.executor_roots(procs)}
    live = [p for p in col.descendants(procs, roots) if p.name.lower() in ("claude.exe", "claude")]
    if live:
        return f"execucao ativa: Claude pid {live[0].pid} e filho do executor; espere terminar (diagnostico copiavel)"
    return None


def task_has_execute(run: Callable[[str], tuple[int, str]] = run_ps) -> bool | None:
    """A acao da tarefa tem `-Execute`? None = nao observavel. Se tiver, a barra RECUSA reiniciar (nada de execucao pela barra)."""
    rc, out = run(f"(Get-ScheduledTask -TaskName '{col.TASK_NAME}' -ErrorAction Stop).Actions | ForEach-Object {{ $_.Arguments }}")
    if rc != 0:
        return None
    return "-execute" in out.lower()


def _wait_gone(procs_fn, wait_s: float, sleep) -> bool:
    """True se nenhum executor segue vivo (prova que o InstanceLock foi solto antes de qualquer novo start)."""
    waited = 0.0
    while True:
        procs = procs_fn()
        if procs is not None and not col.executor_roots(procs):
            return True
        if waited >= wait_s:
            return False
        sleep(1.0)
        waited += 1.0


def preflight(procs_fn, run: Callable[[str], tuple[int, str]], gov: Path | None, confirmed: bool, verb: str) -> Result | None:
    """Preflight UNICO e fail-closed de parar/reiniciar (mesma politica nas duas operacoes). Devolve a recusa, ou None se pode seguir:
    confirmacao, kill switch, acao da tarefa legivel e SEM -Execute (modo DRY_RUN garantido), tabela de processos observavel e
    nenhuma execucao ativa. A parada de emergencia (fora da barra) esta documentada em docs/engineering/agent_bar.md."""
    if not confirmed:
        return Result(False, f"confirmacao necessaria para {verb} o listener")
    g = gov or col.default_gov_dir()
    if (g / "EXECUTOR_DISABLED").exists():
        return Result(False, f"{verb}: recusado - kill switch (EXECUTOR_DISABLED) ativo; remova-o manualmente se for intencional")
    ex = task_has_execute(run)
    if ex is None:
        return Result(False, f"{verb}: recusado - nao foi possivel ler a acao da tarefa (nao provo que e DRY_RUN)")
    if ex:
        return Result(False, f"{verb}: recusado - a tarefa tem -Execute; a barra so controla o listener em DRY_RUN")
    procs = procs_fn()
    why = active_execution(procs)
    if why:
        return Result(False, f"{verb}: recusado - {why}")
    return None


def stop_listener(procs_fn=col.list_processes, run: Callable[[str], tuple[int, str]] = run_ps, confirmed: bool = False,
                  gov: Path | None = None, wait_s: float = 15.0, sleep=time.sleep) -> Result:
    refusal = preflight(procs_fn, run, gov, confirmed, "parada")
    if refusal:
        return refusal
    rc, out = run(f"Stop-ScheduledTask -TaskName '{col.TASK_NAME}' -ErrorAction Stop")
    if rc != 0:
        return Result(False, f"Stop-ScheduledTask falhou: {out[:200]}")
    if not _wait_gone(procs_fn, wait_s, sleep):
        return Result(False, f"tarefa parada, mas o executor ainda esta vivo apos {wait_s:.0f}s (nao reinicie; veja o diagnostico)")
    return Result(True, "listener parado (executor encerrado e lock liberado)")


def restart_listener_dry_run(procs_fn=col.list_processes, run: Callable[[str], tuple[int, str]] = run_ps, confirmed: bool = False,
                             gov: Path | None = None, wait_s: float = 15.0, sleep=time.sleep) -> Result:
    """Mesmo preflight do Stop; so inicia se o executor antigo SUMIU (nunca 2a instancia: respeita o InstanceLock)."""
    refusal = preflight(procs_fn, run, gov, confirmed, "reinicio")
    if refusal:
        return refusal
    procs = procs_fn()
    if procs is not None and col.executor_roots(procs):
        rc, out = run(f"Stop-ScheduledTask -TaskName '{col.TASK_NAME}' -ErrorAction Stop")
        if rc != 0:
            return Result(False, f"Stop-ScheduledTask falhou: {out[:200]}")
        if not _wait_gone(procs_fn, wait_s, sleep):
            return Result(False, f"o executor antigo ainda esta vivo apos {wait_s:.0f}s: nao inicio outro (InstanceLock)")
    rc, out = run(f"Start-ScheduledTask -TaskName '{col.TASK_NAME}' -ErrorAction Stop")
    return Result(rc == 0, "tarefa reiniciada em DRY_RUN" if rc == 0 else f"Start-ScheduledTask falhou: {out[:200]}")


def open_url(url: str | None, opener: Callable[[str], object] | None = None) -> Result:
    if not url or not url.startswith("https://github.com/"):
        return Result(False, "URL indisponivel ou fora de github.com")
    (opener or webbrowser.open)(url)                                           # resolvido na chamada (testavel)
    return Result(True, url)


def open_path(path: Path, opener: Callable[[str], object] | None = None) -> Result:
    if not path.exists():
        return Result(False, f"nao existe: {path}")
    (opener or os.startfile)(str(path))  # type: ignore[attr-defined]  # Windows
    return Result(True, str(path))


def workspace_root() -> Path:
    """Raiz do workspace (`E:\\Claude`): o mesmo nivel para o checkout principal e para worktrees (`<raiz>/apps|worktrees/<repo>/agent_bar`)."""
    env = os.environ.get("AGENT_BAR_WORKSPACE")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:                                  # workspace: a primeira pasta acima que tem `ops/launchers`
        if (parent / "ops" / "launchers").is_dir():
            return parent
    return Path.cwd()


def progress_command(which: Callable[[str], str | None] = shutil.which) -> tuple[str, list[str], Path] | None:
    """(programa, argumentos, cwd) do passo FP-060: `claude -p` em modo auto lendo a missao `ops/launchers/fp060-passo.md`, saida `stream-json` (o painel a mostra legivel).
    A sessao interativa pedia login ao abrir pela barra; o modo sem tela usa a mesma conta e funciona. `None` = CLI nao encontrada (nunca inventa um caminho)."""
    exe = which("claude")
    if not exe:
        return None
    found = Path(exe)
    native = found.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"      # o .cmd do npm nao e um executavel direto
    root = workspace_root()
    mission = Path(os.environ["AGENT_BAR_MISSION"]) if os.environ.get("AGENT_BAR_MISSION") else root / "ops" / "launchers" / "fp060-passo.md"
    if not mission.is_file():                                                       # sem missao nao ha o que executar: recusa em vez de pedir ao claude para ler um arquivo inexistente
        return None
    prompt = f"Leia {mission} e execute exatamente essa missao: um passo para reduzir os PRs abertos da FP-060, respeitando as regras duras do arquivo."
    return (str(native) if native.is_file() else str(found),
            ["-p", prompt, "--permission-mode", "auto", "-n", "FP-060 passo", "--output-format", "stream-json", "--verbose"], root)


def step_running(procs_fn: Callable[[], list[col.Proc] | None] = col.list_processes) -> bool:
    """Ja ha um claude executando a missao (aberto por outra janela ou pelo atalho)? Conveniencia contra dois passos simultaneos, nao barreira de seguranca."""
    procs = procs_fn()
    if procs is None:
        return False
    return any(col.STEP_RE.search(p.cmdline) and p.name.lower() in ("claude.exe", "claude", "node.exe", "node") for p in procs)


def kill_tree(pid: int) -> bool:
    """Encerra a arvore do PASSO que o painel iniciou, por PID (nunca por nome de imagem)."""
    r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))], capture_output=True, creationflags=col.NO_WINDOW)
    return r.returncode == 0


def _summary(tool_input: object) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "pattern", "path", "description", "url"):
        if tool_input.get(key):
            return " ".join(str(tool_input[key]).split())[:130]
    return json.dumps(tool_input, ensure_ascii=False)[:130]


def format_stream_line(line: str) -> list[str]:
    """Uma linha do `stream-json` do `claude -p` -> linhas legiveis do terminal do painel (texto do agente, ferramentas e resultado). Eventos sem interesse = []."""
    text = line.strip()
    if not text:
        return []
    try:
        ev = json.loads(text)
    except ValueError:
        return [text]
    if not isinstance(ev, dict):
        return [text]
    kind = ev.get("type")
    if kind == "system" and ev.get("subtype") == "init":
        return ["== sessao iniciada =="]
    if kind == "assistant":
        out = []
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                out.append(str(block["text"]).strip())
            elif block.get("type") == "tool_use":
                out.append(f"  > {block.get('name')}: {_summary(block.get('input'))}")
        return out
    if kind == "result":
        secs = (ev.get("duration_ms") or 0) / 1000
        out = [f"== FIM ({'ERRO' if ev.get('is_error') else 'ok'}, {secs:.0f}s, turnos {ev.get('num_turns', '?')}) =="]
        if ev.get("result"):
            out.append(str(ev["result"]).strip())
        return out
    return []


def latest_log(gov: Path) -> Path | None:
    """Log mais util: o ultimo log de execucao do executor, senao o log de ciclos do bridge."""
    return col._latest_log(gov)  # noqa: SLF001


def diagnostic_text(snap: Snapshot) -> str:
    return snap.diagnostic()
