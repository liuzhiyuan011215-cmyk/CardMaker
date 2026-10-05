"""第三步：生成中。显示总进度、当前步骤、已用时间、预计剩余时间，可以取消。"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QProgressBar, QPushButton, QVBoxLayout, QWidget

from ..i18n import tr
from ..progress import CHECK, EXTRACT, ProgressEvent
from .widgets import label

EXTRACT_SHARE = 15  # 进度条里「提取文字」阶段占的百分比，其余给「生成卡片」


def format_seconds(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class ProgressPage(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)
        layout.addWidget(label("第三步：正在生成卡片", "PageTitle"))
        layout.addWidget(label("可以去喝口水，完成后会自动进入下一步。", "Hint"))
        layout.addSpacing(10)

        self.stage = label("准备中…", "Big")
        layout.addWidget(self.stage)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)
        stats = QHBoxLayout()
        self.time_label = label("", "Hint")
        self.cards_label = label("", "Hint")
        stats.addWidget(self.time_label)
        stats.addStretch()
        stats.addWidget(self.cards_label)
        layout.addLayout(stats)

        self.log = QListWidget()
        self.log.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.log, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.cancel_button = QPushButton(tr("取消"))
        self.cancel_button.clicked.connect(self._cancel)
        bottom.addWidget(self.cancel_button)
        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._start = self._gen_start = 0.0
        self._gen_done = self._gen_total = 0

    # ------------------------------------------------------------------
    def start(self) -> None:
        self.log.clear()
        self.bar.setValue(0)
        self.stage.setText(tr("① 正在读取文件…"))
        self.cards_label.setText("")
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText(tr("取消"))
        self._start = time.monotonic()
        self._gen_start = 0.0
        self._gen_done = self._gen_total = 0
        self._tick()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def on_progress(self, event: ProgressEvent) -> None:
        total = max(event.total, 1)
        if event.stage == EXTRACT:
            self.stage.setText(tr(f"① 读取文字：{event.done}/{event.total} 页"))
            self.bar.setValue(int(EXTRACT_SHARE * event.done / total))
        elif event.stage == CHECK:  # 卡片已经生成完了，进度条和卡片数不动
            self.stage.setText(tr(f"③ 用词典核对释义：{event.done}/{event.total}"))
        else:
            if not self._gen_start:
                self._gen_start = time.monotonic()
            self._gen_done, self._gen_total = event.done, event.total
            self.stage.setText(tr(f"② 生成卡片：{event.done}/{event.total} 页"))
            self.bar.setValue(EXTRACT_SHARE + int((100 - EXTRACT_SHARE) * event.done / total))
            self.cards_label.setText(tr(f"已生成 {event.cards} 张卡片"))
        self.log.addItem(tr(event.message))
        self.log.scrollToBottom()
        self._tick()

    # ------------------------------------------------------------------
    def _cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(tr("正在取消…"))
        self.log.addItem(tr("正在取消：等当前这一页处理完就停止，已经生成的卡片会保留。"))
        self.cancel_requested.emit()

    def _tick(self) -> None:
        elapsed = time.monotonic() - self._start
        text = f"已用 {format_seconds(elapsed)}"
        if self._gen_done and self._gen_total > self._gen_done:
            per_page = (time.monotonic() - self._gen_start) / self._gen_done
            text += f"  ·  预计还需 {format_seconds(per_page * (self._gen_total - self._gen_done))}"
        self.time_label.setText(tr(text))
