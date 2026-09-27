"""第二步：选择用途（模板）和几个简单选项。

选项表单根据模板 YAML 里的 params 自动生成：有 choices 的显示下拉框，bool 显示勾选框，
int 显示数字框——所以用户自定义的新模板不用改界面代码。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..config import load_settings
from ..ingest.pdf import PdfInfo
from ..pipeline.runner import GenerateOptions
from ..templates.loader import ParamSpec, Template, list_templates
from .widgets import Tile, label

# 粗略估算用：每页大约的 token 数和秒数（给用户一个量级概念）
TOKENS_PER_PAGE = 4000
SECONDS_PER_PAGE = 12
OCR_SECONDS_PER_PAGE = 3


def make_param_widget(spec: ParamSpec) -> tuple[QWidget, Callable[[], object]]:
    """根据参数声明生成输入控件，返回 (控件, 取值函数)。"""
    if spec.type == "bool":
        box = QCheckBox()
        box.setChecked(bool(spec.default))
        return box, box.isChecked
    if spec.type == "int":
        spin = QSpinBox()
        spin.setRange(0, 999)
        spin.setValue(int(spec.default or 0))
        return spin, spin.value
    if spec.choices:
        combo = QComboBox()
        combo.setEditable(True)  # 下拉里没有的也可以自己输入，比如「TOPIK 4级 偏口语」
        combo.addItems(spec.choices)
        combo.setCurrentText(str(spec.default))
        return combo, combo.currentText
    edit = QLineEdit(str(spec.default))
    return edit, edit.text


class OptionsPage(QWidget):
    back_requested = Signal()
    start_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.templates = list_templates()
        self.template: Template | None = None
        self.info: PdfInfo | None = None
        self._getters: dict[str, Callable[[], object]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(14)
        layout.addWidget(label("第二步：你想做什么卡片？", "PageTitle"))
        self.doc_label = label("", "Hint")
        layout.addWidget(self.doc_label)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.tiles: list[Tile] = []
        for i, tpl in enumerate(self.templates):
            tile = Tile(tpl.icon, tpl.name, tpl.description)
            tile.clicked.connect(lambda t=tpl: self.select_template(t))
            grid.addWidget(tile, i // 3, i % 3)
            self.tiles.append(tile)
        layout.addLayout(grid)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel_layout = QHBoxLayout(panel)
        panel_layout.setContentsMargins(22, 16, 22, 16)
        panel_layout.setSpacing(40)
        self.param_form = QFormLayout()
        self.param_form.setHorizontalSpacing(14)
        self.param_form.setVerticalSpacing(10)
        panel_layout.addLayout(self.param_form, 1)

        common = QFormLayout()
        common.setHorizontalSpacing(14)
        common.setVerticalSpacing(10)
        page_row = QHBoxLayout()
        self.page_mode = QComboBox()
        self.page_mode.addItems(["全部页面", "指定范围"])
        self.page_from, self.page_to = QSpinBox(), QSpinBox()
        for spin in (self.page_from, self.page_to):
            spin.setRange(1, 1)
        page_row.addWidget(self.page_mode)
        self.to_label = label("到")
        page_row.addWidget(self.page_from)
        page_row.addWidget(self.to_label)
        page_row.addWidget(self.page_to)
        page_row.addStretch()
        common.addRow("处理哪些页", page_row)
        self.max_cards = QSpinBox()
        self.max_cards.setRange(0, 5000)
        self.max_cards.setSpecialValueText("不限")
        self.max_cards.setSuffix(" 张")
        common.addRow("最多生成", self.max_cards)
        panel_layout.addLayout(common, 1)
        layout.addWidget(panel)

        self.estimate = label("", "Hint", wrap=True)
        layout.addWidget(self.estimate)
        layout.addStretch()

        bottom = QHBoxLayout()
        back = QPushButton("←  上一步")
        back.clicked.connect(self.back_requested)
        bottom.addWidget(back)
        bottom.addStretch()
        self.start_button = QPushButton("开始生成")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start_requested)
        bottom.addWidget(self.start_button)
        layout.addLayout(bottom)

        self.page_mode.currentIndexChanged.connect(self._update_pages)
        for spin in (self.page_from, self.page_to):
            spin.valueChanged.connect(self._update_estimate)
        if self.templates:
            self.select_template(self.templates[0])

    # ------------------------------------------------------------------
    def set_document(self, path: Path, info: PdfInfo) -> None:
        self.info = info
        self.doc_label.setText(f"文件：{path.name}（共 {info.page_count} 页）")
        for spin in (self.page_from, self.page_to):
            spin.setRange(1, max(1, info.page_count))
        self.page_from.setValue(1)
        self.page_to.setValue(info.page_count)
        self.page_mode.setCurrentIndex(0)
        self._update_pages()

    def select_template(self, template: Template) -> None:
        self.template = template
        for tile, tpl in zip(self.tiles, self.templates):
            tile.set_checked(tpl is template)
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self._getters = {}
        defaults = {"meaning_language": load_settings().meaning_language}
        for name, spec in template.params.items():
            if name in defaults:
                spec = spec.model_copy(update={"default": defaults[name]})
            widget, getter = make_param_widget(spec)
            widget.setToolTip(spec.description)
            self.param_form.addRow(spec.label, widget)
            self._getters[name] = getter

    def selected_pages(self) -> list[int] | None:
        if self.page_mode.currentIndex() == 0:
            return None
        start, end = sorted((self.page_from.value(), self.page_to.value()))
        return list(range(start, end + 1))

    def collect(self) -> tuple[Template, dict, GenerateOptions]:
        params = {name: getter() for name, getter in self._getters.items()}
        settings = load_settings()
        options = GenerateOptions(pages=self.selected_pages(), max_cards=self.max_cards.value(),
                                  ocr_langs=settings.ocr.langs, dpi=settings.ocr.dpi)
        return self.template, params, options

    # ------------------------------------------------------------------
    def _update_pages(self) -> None:
        custom = self.page_mode.currentIndex() == 1
        for widget in (self.page_from, self.to_label, self.page_to):
            widget.setVisible(custom)
        self._update_estimate()

    def _update_estimate(self) -> None:
        if not self.info:
            return
        pages = self.selected_pages()
        n = len(pages) if pages else self.info.page_count
        seconds = n * (SECONDS_PER_PAGE + (OCR_SECONDS_PER_PAGE if self.info.kind != "文字版" else 0))
        tokens = n * TOKENS_PER_PAGE
        minutes = max(1, round(seconds / 60))
        self.estimate.setText(f"预计处理 {n} 页，大约 {minutes} 分钟，消耗约 {tokens / 10000:.1f} 万 token。"
                              "页数多的书建议先选几页试试效果。")
