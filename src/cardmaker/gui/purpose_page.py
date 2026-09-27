"""第一步：选择用途（领域）。每个用途是一张大卡片，写清楚适合什么资料、能做哪种卡。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..storage import TaskInfo
from ..templates.loader import TemplateGroup, list_groups
from .widgets import Tile, label

CARD_KINDS = {"vocab": "单词卡（外语→释义 / 释义→外语）", "cloze": "填空卡", "mcq": "选择题"}


def kinds_text(group: TemplateGroup) -> str:
    kinds = list(dict.fromkeys(CARD_KINDS[v.note_type] for v in group.variants))
    return "可做：" + " · ".join(kinds)


class PurposePage(QWidget):
    next_requested = Signal()
    resume_requested = Signal(int)  # 继续某个没做完的任务
    edit_template_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.groups: list[TemplateGroup] = []
        self.group: TemplateGroup | None = None
        self.tiles: list[Tile] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 24)
        layout.setSpacing(12)
        layout.addWidget(label("第一步：你想做什么卡片？", "PageTitle"))
        layout.addWidget(label("选一个用途，下一步再选资料。每种用途的出题方式都不一样。", "Hint"))

        # 上次没做完的任务（断点续传）
        self._resume_id: int | None = None
        self.resume_panel = QFrame()
        self.resume_panel.setObjectName("Panel")
        resume_row = QHBoxLayout(self.resume_panel)
        resume_row.setContentsMargins(18, 10, 18, 10)
        self.resume_text = label("", "", wrap=True)
        resume_row.addWidget(self.resume_text, 1)
        ignore = QPushButton("忽略")
        ignore.setObjectName("Link")
        ignore.clicked.connect(self.resume_panel.hide)
        resume_row.addWidget(ignore)
        resume_button = QPushButton("继续生成")
        resume_button.setObjectName("Primary")
        resume_button.clicked.connect(self._resume)
        resume_row.addWidget(resume_button)
        self.resume_panel.hide()
        layout.addWidget(self.resume_panel)

        self.grid = QGridLayout()
        self.grid.setSpacing(14)
        layout.addLayout(self.grid)
        layout.addStretch()

        bottom = QHBoxLayout()
        edit = QPushButton("查看 / 编辑模板…")
        edit.setObjectName("Link")
        edit.setToolTip("每种用途的提示词、输出格式、卡片字段都在模板文件里，可以查看和修改")
        edit.clicked.connect(self.edit_template_requested)
        bottom.addWidget(edit)
        bottom.addStretch()
        self.next_button = QPushButton("下一步  →")
        self.next_button.setObjectName("Primary")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.next_requested)
        bottom.addWidget(self.next_button)
        layout.addLayout(bottom)
        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        """重新读取模板（编辑模板后调用）。"""
        current = self.group.id if self.group else None
        for tile in self.tiles:
            tile.deleteLater()
        self.groups, self.tiles = list_groups(), []
        for i, group in enumerate(self.groups):
            tile = Tile(group.icon, group.name, f"{group.description}\n{kinds_text(group)}")
            tile.clicked.connect(lambda g=group: self.select(g))
            tile.double_clicked.connect(lambda g=group: (self.select(g), self.next_requested.emit()))
            self.grid.addWidget(tile, i // 3, i % 3)
            self.tiles.append(tile)
        self.group = None
        for group in self.groups:
            if group.id == current:
                self.select(group)

    def select(self, group: TemplateGroup) -> None:
        self.group = group
        for tile, g in zip(self.tiles, self.groups):
            tile.set_checked(g is group)
        self.next_button.setEnabled(True)

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

    def _resume(self) -> None:
        if self._resume_id is not None:
            self.resume_requested.emit(self._resume_id)
