"""第一步：选择用途。两张大卡片，点一下（或用键盘选中后按回车）就进入下一步。

先只放单词的两种用途：读书笔记、法律、编程等用途已经有模板，但还没评估，做好了再放出来（改 SHOWN 即可）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout

from ..i18n import tr
from ..storage import TaskInfo
from ..templates.loader import READY_GROUPS, TemplateGroup, list_groups
from .icons import IconView
from .widgets import HoverLift, PageFrame, Picture, button, label, repolish

SHOWN = READY_GROUPS  # 第一步显示哪些用途，按这个顺序
CARDS = {  # 卡片上的标题、插图（assets 里）和三个特点；说明文字用模板里的 description
    "vocab_book": ("单词书 / 教材", "purpose_vocab_book.png", ["扫描件也能用", "照录书上的释义和例句", "词典核对"]),
    "vocab_reading": ("阅读材料里的生词", "purpose_vocab_reading.png",
                      ["按 TOPIK · HSK · IELTS 水平挑词", "附原句和例句", "词典核对"]),
}


def shown_template_ids() -> set[str]:
    """第一步里能选的用途下的模板编号：历史记录和「继续生成」只列这些。"""
    return {t.id for g in list_groups() if g.id in SHOWN for t in g.variants}


class PurposeCard(QFrame):
    """一张用途卡片：图标、标题、说明、特点标签。点一下或按回车发出 activated。"""

    activated = Signal()

    def __init__(self, group: TemplateGroup, parent=None):
        super().__init__(parent)
        self.setObjectName("PurposeCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(250)
        title, picture, features = CARDS.get(group.id, (group.name, "document.png", []))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(0)
        self.go = QFrame()  # 右上角的圆形箭头：平时浅灰，鼠标移到卡片上变黑，提示「点这里进入下一步」
        self.go.setObjectName("GoDot")
        self.go.setFixedSize(32, 32)
        go_layout = QVBoxLayout(self.go)
        go_layout.setContentsMargins(0, 0, 0, 0)
        self.arrow = IconView("arrow_right", 16, "text")
        go_layout.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignCenter)
        top = QHBoxLayout()
        top.addWidget(Picture(picture, 72))
        top.addStretch(1)
        top.addWidget(self.go, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        layout.addSpacing(14)
        layout.addWidget(label(title, "cardtitle"))
        layout.addSpacing(8)
        layout.addWidget(label(group.description, "secondary", wrap=True))
        layout.addStretch(1)
        layout.addSpacing(16)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for text in features:
            chips.addWidget(label(text, "chip"))
        chips.addStretch(1)
        layout.addLayout(chips)
        HoverLift(self)
        self.set_checked(False)

    def set_checked(self, checked: bool) -> None:
        self.setProperty("checked", "true" if checked else "false")
        repolish(self)

    def _set_hot(self, hot: bool) -> None:
        self.go.setProperty("hot", "true" if hot else "false")
        repolish(self.go)
        self.arrow.set_role("on_accent" if hot else "text")

    def enterEvent(self, event):
        self._set_hot(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_hot(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
        else:
            super().keyPressEvent(event)


class PurposePage(PageFrame):
    next_requested = Signal()
    resume_requested = Signal(int)  # 继续某个没做完的任务
    edit_template_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(max_width=960, top=40, bleed=24, parent=parent)
        self.groups: list[TemplateGroup] = []
        self.group: TemplateGroup | None = None
        self.tiles: list[PurposeCard] = []
        self.add_heading("第一步：你想做什么卡片？", "选一种，点一下就进入下一步。")

        # 上次没做完的任务（断点续传）
        self._resume_id: int | None = None
        self.resume_panel = QFrame()
        self.resume_panel.setObjectName("Panel")
        resume_row = QHBoxLayout(self.resume_panel)
        resume_row.setContentsMargins(16, 12, 16, 12)
        resume_row.setSpacing(12)
        resume_row.addWidget(Picture("clock.png", 40))
        self.resume_text = label("", "", wrap=True)
        resume_row.addWidget(self.resume_text, 1)
        ignore = QPushButton(tr("忽略"))
        ignore.setProperty("variant", "ghost")
        ignore.clicked.connect(self.resume_panel.hide)
        resume_row.addWidget(ignore)
        resume_button = QPushButton(tr("继续生成"))
        resume_button.setProperty("variant", "primary")
        resume_button.clicked.connect(self._resume)
        resume_row.addWidget(resume_button)
        self.resume_panel.hide()
        self.body.addWidget(self.resume_panel)
        self.body.addSpacing(16)

        self.grid = QHBoxLayout()
        self.grid.setSpacing(20)
        self.body.addLayout(self.grid)
        self.body.addStretch(1)

        edit = QPushButton(tr("查看 / 编辑模板…"))
        edit.setProperty("variant", "link")
        edit.setToolTip(tr("每种用途的提示词、输出格式、卡片字段都在模板文件里，可以查看和修改"))
        edit.clicked.connect(self.edit_template_requested)
        self.footer.left.addWidget(edit)
        self.next_button = button("下一步", "primary", icon="arrow_right")
        self.next_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # 箭头放在文字右边
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.next_requested)
        self.footer.right.addWidget(self.next_button)
        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        """重新读取模板（编辑模板后调用）。"""
        current = self.group.id if self.group else None
        for tile in self.tiles:
            tile.deleteLater()
        groups = {g.id: g for g in list_groups()}
        self.groups = [groups[gid] for gid in SHOWN if gid in groups]
        self.tiles = []
        for group in self.groups:
            tile = PurposeCard(group)
            tile.activated.connect(lambda g=group: (self.select(g), self.next_requested.emit()))
            self.grid.addWidget(tile, 1)
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
            tr(f"上次的任务还没做完：《{task.book_title}》已完成 {task.chunks_ok}/{task.chunks_total} 页，"
            f"已生成 {task.cards} 张卡片。点「继续生成」从中断的地方接着做，已完成的页不会重复花 token。"))
        self.resume_panel.show()

    def _resume(self) -> None:
        if self._resume_id is not None:
            self.resume_requested.emit(self._resume_id)
