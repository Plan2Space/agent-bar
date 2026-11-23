"""contracts_lock.py — versoes de SPEC consumidas, travadas e conferidas (achado H-015 da auditoria do harness).

`contracts.lock` (raiz do repo) lista cada SPEC do engineering-specs que o agent-bar le: caminho, versao, commit
imutavel, sha256 do arquivo naquele commit e os MAJOR de contrato que o codigo aceita.

Duas conferencias:
- `verify_code()` (roda no CI, sem acesso ao repo privado de SPECs): os MAJOR que o codigo aceita batem com o lock;
- `check_specs(checkout)` (local, `python -m agent_bar.contracts_lock --specs <checkout>`): o arquivo no commit travado
  tem o sha256 do lock (senao: ERRO) e a versao atual da SPEC no ref de origem; MAJOR novo -> ERRO (contrato
  incompativel), MINOR/PATCH novo -> INFO (aditivo; atualize o lock quando for usar o que mudou).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "contracts.lock"
VERSION_ROW = re.compile(r"^\| v(\d+)\.(\d+)\.(\d+)", re.M)


@dataclass(frozen=True)
class Finding:
    level: str                                                               # ERROR | INFO
    spec: str
    message: str


def load_lock(path: Path = LOCK) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "p2s.contracts-lock.v1":
        raise ValueError("contracts.lock: schema desconhecido")
    for e in data.get("consumes") or []:
        for k in ("spec", "version", "ref", "commit", "sha256", "contracts", "reader"):
            if k not in e:
                raise ValueError(f"contracts.lock: {e.get('spec', '?')} sem '{k}'")
        if not re.fullmatch(r"[0-9a-f]{40}", e["commit"]) or not re.fullmatch(r"[0-9a-f]{64}", e["sha256"]):
            raise ValueError(f"contracts.lock: {e['spec']} com commit/sha256 fora do formato")
    return data


def code_majors() -> dict[str, dict[str, set[str]]]:
    """MAJOR de contrato que o codigo aceita, por SPEC (a fonte e o proprio codigo, nao um comentario)."""
    from agent_bar import runs
    from agent_bar.platform import contracts as pc
    return {
        "plan2space/P2S-CONTRACTS-v1.md": {"C3": {runs.SUPPORTED_MAJOR}, "C4": set(runs.GEOMETRY_MAJORS)},
        "agent-bar/P2S-STUDIO-MULTI-AGENT-PLATFORM.md": {
            name: {s["$id"].rsplit(".v", 1)[1]} for name, s in pc.SCHEMAS.items()},
    }


def verify_code(lock: dict | None = None) -> list[Finding]:
    """O MAJOR travado de cada contrato tem de estar entre os que o codigo aceita."""
    lock = lock or load_lock()
    accepted = code_majors()
    out: list[Finding] = []
    for e in lock["consumes"]:
        for contract, major in e["contracts"].items():
            ok = accepted.get(e["spec"], {}).get(contract)
            if ok is None:
                out.append(Finding("ERROR", e["spec"], f"{contract}: o codigo nao declara o que aceita"))
            elif str(major) not in ok:
                out.append(Finding("ERROR", e["spec"], f"{contract}: lock em v{major}, codigo aceita {sorted(ok)}"))
    return out


def _git(checkout: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, check=True).stdout.decode("utf-8")


def latest_version(text: str) -> tuple[int, int, int] | None:
    rows = [tuple(int(x) for x in m.groups()) for m in VERSION_ROW.finditer(text)]
    return max(rows) if rows else None


def check_specs(checkout: Path, lock: dict | None = None, remote: str = "origin") -> list[Finding]:
    lock = lock or load_lock()
    out: list[Finding] = []
    for e in lock["consumes"]:
        spec = e["spec"]
        try:
            pinned = _git(checkout, "show", f"{e['commit']}:{spec}")
        except subprocess.CalledProcessError:
            out.append(Finding("ERROR", spec, f"commit {e['commit'][:12]} sem o arquivo (fetch o checkout?)"))
            continue
        digest = hashlib.sha256(pinned.encode("utf-8")).hexdigest()
        if digest != e["sha256"]:
            out.append(Finding("ERROR", spec, f"sha256 no commit travado diverge do lock ({digest[:12]})"))
        try:
            current = _git(checkout, "show", f"{remote}/{e['ref']}:{spec}")
        except subprocess.CalledProcessError:
            out.append(Finding("ERROR", spec, f"{remote}/{e['ref']} sem o arquivo"))
            continue
        if current == pinned:
            continue
        old, new = latest_version(pinned), latest_version(current)
        if old and new and new[0] != old[0]:
            out.append(Finding("ERROR", spec, f"MAJOR mudou: lock v{'.'.join(map(str, old))}, "
                                              f"{e['ref']} v{'.'.join(map(str, new))} (contrato incompativel)"))
        elif old and new and new > old:
            out.append(Finding("INFO", spec, f"{e['ref']} em v{'.'.join(map(str, new))} (lock v{'.'.join(map(str, old))}): "
                                             "aditivo; atualize o lock quando usar o que mudou"))
        else:
            out.append(Finding("INFO", spec, f"o texto mudou em {e['ref']} desde o commit travado"))
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    findings = verify_code()
    if "--specs" in args:
        findings += check_specs(Path(args[args.index("--specs") + 1]))
    for f in findings:
        print(f"{f.level:5} {f.spec}: {f.message}")
    if not findings:
        print("ok: contracts.lock confere com o codigo" + (" e com o checkout de SPECs" if "--specs" in args else ""))
    return 1 if any(f.level == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
