"""window.py — janela do cockpit ACP (D2): QMainWindow redimensionavel com abas. F2 entrega a aba Chat."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from agent_bar.cockpit.acp import AsyncRuntime
from agent_bar.cockpit.ui.chat import ChatView

TAB_CSS = ("QTabWidget::pane{border:0;background:#16161a}QTabBar::tab{background:#202026;color:#bdbdc4;padding:6px 14px;"
           "border-top-left-radius:6px;border-top-right-radius:6px;margin-right:2px}"
           "QTabBar::tab:selected{background:#2c2c34;color:#ffffff}")


class CockpitWindow(QMainWindow):
    def __init__(self, **chat_kwargs) -> None:
        super().__init__()
        self.setWindowTitle("Cockpit ACP — Agent Bar")
        self.resize(980, 720)
        self.runtime = AsyncRuntime("cockpit")
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(TAB_CSS)
        self.chat = ChatView(runtime=self.runtime, **chat_kwargs)
        self.tabs.addTab(self.chat, "Chat")
        self.setCentralWidget(self.tabs)
        self.setStyleSheet("QMainWindow{background:#16161a}")
        self._closed = False

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.chat.shutdown()
        self.runtime.stop()

    def closeEvent(self, e) -> None:                                            # noqa: N802
        self.shutdown()
        super().closeEvent(e)
