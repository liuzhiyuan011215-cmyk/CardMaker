"""桌面程序入口：cardmaker-gui 或 python -m cardmaker.gui"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..log import setup_logging
from .main_window import MainWindow
from .style import STYLESHEET

ICON = Path(__file__).parent / "assets" / "icon.png"


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("CardMaker")
    app.setApplicationDisplayName("CardMaker")
    app.setStyle("Fusion")
    try:  # 统一用浅色外观，不跟随系统深色模式，演示时效果稳定
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet(STYLESHEET)
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    return app


def main() -> int:
    setup_logging()
    app = create_app()
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()  # 从 Finder 双击打开时把窗口放到最前面
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
