"""第一步：选择文件（拖进来、点按钮选，或者直接粘贴一段文字）。"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..config import app_home
from ..ingest.pdf import PdfInfo, inspect_pdf
from ..storage import TaskInfo
from .widgets import DropZone, label

# 演示用的示例文件（自己编写，无版权问题），方便没有现成 PDF 时直接试用
DEMO_DIR = Path(__file__).resolve().parents[3] / "samples" / "demo"
DEMO_FILES = [("韩语文章 · 2 页 · 文字版", "korean_reading.pdf"),
              ("中韩单词书 · 1 页 · 扫描版", "vocab_book_scan.pdf"),
              ("英语短文 · 1 页 · 文字版", "english_reading.pdf")]


class PasteDialog(QDialog):
    """没有文件时，直接粘贴一段文字（比如从网页复制的读书笔记）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("粘贴文字")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(label("把要做成卡片的文字粘贴到下面：", "Big"))
        self.title = QLineEdit()
        self.title.setPlaceholderText("书名或标题（可不填）")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("在这里粘贴…")
        layout.addWidget(self.title)
        layout.addWidget(self.text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("使用这段文字")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save_to_file(self) -> Path | None:
        text = self.text.toPlainText().strip()
        if not text:
            return None
        name = self.title.text().strip() or "粘贴的文字"
        folder = app_home() / "pasted"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path.write_text(text, encoding="utf-8")
        return path


class ImportPage(QWidget):
    next_requested = Signal()
    resume_requested = Signal(int)  # 继续某个没做完的任务

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.path: Path | None = None
        self.info: PdfInfo | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)
        layout.addWidget(label("第一步：选择要做成卡片的文件", "PageTitle"))
        layout.addWidget(label("教材、单词书、小说、文章都可以，扫描版也行。", "Hint"))

        # 上次没做完的任务（断点续传）
        self._resume_id: int | None = None
        self.resume_panel = QFrame()
        self.resume_panel.setObjectName("Panel")
        resume_row = QHBoxLayout(self.resume_panel)
        resume_row.setContentsMargins(18, 12, 18, 12)
        self.resume_text = label("", "", wrap=True)
        resume_row.addWidget(self.resume_text, 1)
        ignore = QPushButton("忽略")
        ignore.setObjectName("Link")
        ignore.clicked.connect(self.resume_panel.hide)
        resume_row.addWidget(ignore)
        resume_button = QPushButton("继续生成")
        resume_button.setObjectName("Primary")
        resume_button.clicked.connect(lambda: self._resume_id is not None and self.resume_requested.emit(self._resume_id))
        resume_row.addWidget(resume_button)
        self.resume_panel.hide()
        layout.addWidget(self.resume_panel)

        self.drop = DropZone()
        self.drop.file_chosen.connect(self.set_file)
        self.drop.browse_clicked.connect(self.browse)
        layout.addWidget(self.drop, 1)

        # 选好文件后显示的信息卡片
        self.file_panel = QFrame()
        self.file_panel.setObjectName("Panel")
        row = QHBoxLayout(self.file_panel)
        row.setContentsMargins(22, 18, 22, 18)
        icon = label("📄")
        icon.setStyleSheet("font-size: 40px;")
        row.addWidget(icon)
        info_col = QVBoxLayout()
        self.file_name = label("", "Big")
        self.file_detail = label("", "Hint")
        info_col.addWidget(self.file_name)
        info_col.addWidget(self.file_detail)
        row.addLayout(info_col, 1)
        change = QPushButton("换一个文件")
        change.clicked.connect(self.clear)
        row.addWidget(change)
        self.file_panel.hide()
        layout.addWidget(self.file_panel)

        bottom = QHBoxLayout()
        paste = QPushButton("没有文件？直接粘贴一段文字")
        paste.setObjectName("Link")
        paste.clicked.connect(self.paste_text)
        bottom.addWidget(paste)
        demos = [(name, DEMO_DIR / file) for name, file in DEMO_FILES if (DEMO_DIR / file).exists()]
        if demos:
            demo_button = QPushButton("用示例文件试试 ▾")
            demo_button.setObjectName("Link")
            menu = QMenu(demo_button)
            for name, path in demos:
                menu.addAction(name, lambda p=path: self.set_file(p))
            demo_button.setMenu(menu)
            bottom.addWidget(demo_button)
        bottom.addStretch()
        self.next_button = QPushButton("下一步  →")
        self.next_button.setObjectName("Primary")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.next_requested)
        bottom.addWidget(self.next_button)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    def show_unfinished(self, task: TaskInfo | None) -> None:
        """显示「上次的任务还没做完」提醒；task 为 None 时隐藏。"""
        self._resume_id = task.id if task else None
        if task is None:
            self.resume_panel.hide()
            return
        self.resume_text.setText(
            f"⏸  上次的任务还没做完：《{task.book_title}》已完成 {task.chunks_ok}/{task.chunks_total} 页，"
            f"已生成 {task.cards} 张卡片。点「继续生成」从中断的地方接着做，已完成的页不会重复花 token。")
        self.resume_panel.show()

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", str(Path.home()),
                                              "PDF 或文本文件 (*.pdf *.txt *.md)")
        if path:
            self.set_file(Path(path))

    def paste_text(self) -> None:
        dialog = PasteDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.save_to_file()
            if path:
                self.set_file(path)

    def set_file(self, path: Path) -> None:
        try:
            if path.suffix.lower() == ".pdf":
                self.info = inspect_pdf(path)
                kind = {"文字版": "文字版", "扫描版": "扫描版（会自动识别文字，稍慢一些）",
                        "混合": "部分页面是扫描的（会自动识别）"}[self.info.kind]
                detail = f"共 {self.info.page_count} 页 · {kind}"
            else:
                text = path.read_text(encoding="utf-8-sig")
                self.info = PdfInfo(page_count=1, title=path.stem, kind="文本")
                detail = f"文本 · {len(text):,} 字"
        except Exception as exc:  # 损坏的 PDF、编码不对的文本等
            QMessageBox.warning(self, "打不开这个文件", f"{path.name}\n\n{exc}")
            return
        self.path = path
        self.file_name.setText(path.name)
        self.file_detail.setText(detail)
        self.drop.hide()
        self.file_panel.show()
        self.next_button.setEnabled(True)

    def clear(self) -> None:
        self.path, self.info = None, None
        self.file_panel.hide()
        self.drop.show()
        self.next_button.setEnabled(False)
