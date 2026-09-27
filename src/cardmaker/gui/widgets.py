"""通用小组件：步骤条、文件拖放区、可点选的模板卡片。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

SUPPORTED_SUFFIXES = (".pdf", ".txt", ".md")


def repolish(widget: QWidget) -> None:
    """动态属性改变后让样式表重新生效。"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


class StepIndicator(QWidget):
    """顶部的步骤条：① 选择文件 — ② 选择用途 — ③ 生成卡片 — ④ 检查并导入"""

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._labels = []
        for i, text in enumerate(steps):
            if i:
                line = QFrame()
                line.setObjectName("StepLine")
                line.setFixedSize(36, 2)
                layout.addWidget(line)
            lbl = label(f"{'①②③④⑤⑥'[i]} {text}", "Step")
            layout.addWidget(lbl)
            self._labels.append(lbl)
        layout.addStretch()
        self.set_current(0)

    def set_current(self, index: int) -> None:
        for i, lbl in enumerate(self._labels):
            lbl.setProperty("state", "done" if i < index else "current" if i == index else "todo")
            repolish(lbl)


class DropZone(QFrame):
    """把文件拖进来，或点按钮选择文件。"""

    file_chosen = Signal(Path)
    browse_clicked = Signal()

    def __init__(self, parent=None, title: str = "把 PDF 或文本文件拖到这里",
                 hint: str = "支持文字版和扫描版 PDF，自动识别中文、韩文、英文", min_height: int = 260):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(min_height)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)
        icon = label("📄", "")
        icon.setStyleSheet("font-size: 56px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label(title, "Big")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = label(hint, "Hint", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("选择文件…")
        button.clicked.connect(self.browse_clicked)
        layout.addWidget(icon)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

    @staticmethod
    def _first_supported(event) -> Path | None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                return path
        return None

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        repolish(self)

    def dragEnterEvent(self, event):
        if self._first_supported(event):
            event.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, event):
        self._set_active(False)

    def dropEvent(self, event):
        self._set_active(False)
        path = self._first_supported(event)
        if path:
            self.file_chosen.emit(path)


class Tile(QFrame):
    """一张可点选的大卡片，用来选用途。双击直接进入下一步。"""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, icon: str, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(250, 120)
        self.setMaximumHeight(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        head = QHBoxLayout()
        emoji = label(icon)
        emoji.setStyleSheet("font-size: 26px;")
        head.addWidget(emoji)
        head.addWidget(label(title, "TileTitle"))
        head.addStretch()
        layout.addLayout(head)
        layout.addWidget(label(text, "TileText", wrap=True))
        layout.addStretch()
        self.set_checked(False)

    def set_checked(self, checked: bool) -> None:
        self.setProperty("checked", "true" if checked else "false")
        repolish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
