"""composer.py — caixa de mensagem do Chat (SPEC P2S-STUDIO-MULTI-AGENT-WORKSPACE-UX-v1, F2).

| Entrada                          | Acao                                                      |
|----------------------------------|-----------------------------------------------------------|
| Enter                            | envia (ou quebra linha, se a preferencia "Enter envia" estiver desligada) |
| Shift+Enter                      | quebra linha                                              |
| Ctrl+Enter                       | envia, sempre                                             |
| Enter durante composicao IME     | nao envia (o Enter confirma a composicao)                 |
| Enter com sugestao aberta        | confirma a sugestao (gancho `suggestion_open`)            |
| Enter com mensagem vazia         | nao envia (quem decide e o `ChatView.send`, que ignora vazio) |

Quem envia e o sinal `submit`; o `ChatView` impede envio duplicado (turno em curso) e devolve o rascunho se falhar.
A preferencia fica em `preferences.json` no diretorio de dados do cockpit (sobrevive ao reinicio).
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QPlainTextEdit

ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


class Composer(QPlainTextEdit):
    submit = Signal()

    def __init__(self, enter_sends: bool = True, suggestion_open: Callable[[], bool] = lambda: False) -> None:
        super().__init__()
        self.enter_sends = enter_sends
        self.suggestion_open = suggestion_open
        self._preedit = False
        self.setTabChangesFocus(True)                                        # Tab sai da caixa (navegacao por teclado)
        self.setAccessibleName("Mensagem para o agente")
        self._update_placeholder()

    def set_enter_sends(self, on: bool) -> None:
        self.enter_sends = bool(on)
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        self.setPlaceholderText("Escreva para o agente… (Enter envia · Shift+Enter quebra linha)" if self.enter_sends
                                else "Escreva para o agente… (Ctrl+Enter envia · Enter quebra linha)")

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:              # noqa: N802 - API do Qt
        self._preedit = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:                          # noqa: N802 - API do Qt
        if event.key() not in ENTER_KEYS or self.suggestion_open():
            super().keyPressEvent(event)
            return
        if self._preedit:                                                    # o Enter pertence a composicao IME
            event.accept()
            return
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self.insertPlainText("\n")                                       # quebra de linha comum (nao U+2028)
        elif mods & Qt.KeyboardModifier.ControlModifier or self.enter_sends:
            self.submit.emit()
        else:
            self.insertPlainText("\n")
        event.accept()


# ------------------------------------------------------------------------------------------------ preferencia
def load_enter_sends(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return bool(data.get("enter_sends", True)) if isinstance(data, dict) else True


def save_enter_sends(path: Path, on: bool) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data["enter_sends"] = bool(on)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
