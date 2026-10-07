"""历史记录：以前的任务都在这里，可以打开看卡片，没做完的可以继续（断点续传）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ..i18n import tr
from ..pipeline.result import cost_of, money
from ..storage import TaskInfo, TaskStore, status_text
from ..templates.loader import list_templates
from .widgets import label

COLUMNS = ["时间", "书名", "用途", "进度", "卡片", "Token", "费用（元）", "状态"]


def cost_cell(task: TaskInfo) -> str:
    cost = cost_of(task.usage, task.options)
    return "本地免费" if task.options.get("local_model") else "—" if cost is None else money(cost)


class HistoryDialog(QDialog):
    open_requested = Signal(int)  # 任务编号
    resume_requested = Signal(int)

    def __init__(self, parent=None, store: TaskStore | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("历史记录"))
        self.resize(920, 520)
        self.store = store or TaskStore()
        self.tasks: list[TaskInfo] = self.store.list_tasks()
        names = {t.id: t.name for t in list_templates()}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(label("历史记录", "PageTitle"))
        layout.addWidget(label("每一页处理完都会自动保存。没做完的任务可以继续，已经完成的页不会重复花 token。", "Hint"))

        self.table = QTableWidget(len(self.tasks), len(COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, task in enumerate(self.tasks):
            values = [task.created_at[5:16], task.book_title, names.get(task.template_id, task.template_id),
                      f"{task.chunks_ok}/{task.chunks_total} 页", str(task.cards), f"{task.total_tokens:,}",
                      cost_cell(task), status_text(task)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(tr(value))
                if col in (3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda *_: self._open())
        layout.addWidget(self.table, 1)
        if not self.tasks:
            layout.addWidget(label("还没有任何记录。", "Hint"))

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.open_button = QPushButton(tr("查看卡片"))
        self.open_button.clicked.connect(self._open)
        self.resume_button = QPushButton(tr("继续生成"))
        self.resume_button.setObjectName("Primary")
        self.resume_button.clicked.connect(self._resume)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.reject)
        for button in (close, self.open_button, self.resume_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        if self.tasks:
            self.table.selectRow(0)
        self._update_buttons()

    def selected_task(self) -> TaskInfo | None:
        rows = self.table.selectionModel().selectedRows()
        return self.tasks[rows[0].row()] if rows else None

    def _update_buttons(self) -> None:
        task = self.selected_task()
        self.open_button.setEnabled(bool(task and task.cards))
        self.resume_button.setEnabled(bool(task and task.unfinished))

    def _open(self) -> None:
        task = self.selected_task()
        if task and task.cards:
            self.open_requested.emit(task.id)
            self.accept()

    def _resume(self) -> None:
        task = self.selected_task()
        if task and task.unfinished:
            self.resume_requested.emit(task.id)
            self.accept()
