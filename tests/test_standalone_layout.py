"""Layout do repositorio independente: a raiz e `agent_bar/..`, nunca uma pasta acima do projeto."""
from __future__ import annotations

from pathlib import Path

from agent_bar import collectors as col


def test_once_json_writes_utf8_even_when_stdout_is_cp1252(monkeypatch):
    """S00-07 D7: diagnostico redirecionado sai em UTF-8 (antes saia em cp1252 e quebrava acentos e simbolos)."""
    import io
    import sys

    from agent_bar import app

    class Snap:
        def diagnostic(self) -> str:
            return "GPT: Indisponível ✓ ’"

    monkeypatch.setattr(col, "collect_snapshot", lambda **_k: Snap())
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict"))
    assert app.main(["--once-json"]) == 0
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8").strip() == "GPT: Indisponível ✓ ’"
