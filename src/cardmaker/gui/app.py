"""桌面程序入口：cardmaker-gui 或 python -m cardmaker.gui"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QLocale
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..config import load_settings
from ..i18n import set_language
from ..log import setup_logging
from .main_window import MainWindow
from .theme import theme

ICON = Path(__file__).parent / "assets" / "icon.png"


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("CardMaker")
    app.setApplicationDisplayName("CardMaker")
    app.setStyle("Fusion")
    theme.install(app)  # 配色、字号、样式表都在 theme.py；默认浅色，和答辩幻灯片一个调子
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    return app


def main() -> int:
    setup_logging()
    app = create_app()
    # 界面语言：设置里选的；没选过就跟随系统（中文、韩语系统各用各的，其他系统用英语）
    system = {QLocale.Language.Chinese: "zh", QLocale.Language.Korean: "ko"}.get(QLocale.system().language(), "en")
    set_language(load_settings().ui_language or system)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()  # 从 Finder 双击打开时把窗口放到最前面
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
