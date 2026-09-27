"""第二步：选择资料和选项。左边选资料（拖文件、粘贴文字或用示例），右边是这个用途的选项。

选项表单根据模板 YAML 里的 params 自动生成：有 choices 的显示下拉框，bool 显示勾选框，
int 显示数字框——所以用户自定义的新模板不用改界面代码。
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QRadioButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from ..config import app_home, load_settings
from ..ingest.notes import FORMAT_NAMES, PER_PAGE, books_in, parse_notes
from ..ingest.pdf import PdfInfo, inspect_pdf
from ..pipeline.runner import GenerateOptions
from ..templates.loader import ParamSpec, Template, TemplateGroup
from .widgets import DropZone, label

# 示例文件（自己编写，无版权问题），没有现成资料时可以直接试用
DEMO_DIR = Path(__file__).resolve().parents[3] / "samples" / "demo"
_DOCS = [("韩语文章 · 2 页 · 文字版", "korean_reading.pdf"), ("英语短文 · 1 页", "english_reading.pdf"),
         ("中韩单词书 · 1 页 · 扫描版", "vocab_book_scan.pdf"), ("图书馆借阅规定 · 2 页", "law_demo.pdf"),
         ("Python 入门 · 1 页", "code_demo.pdf"), ("印刷术与知识传播 · 1 页", "humanities_demo.pdf")]
DEMO_FILES = {
    "vocab_reading": _DOCS[:2], "vocab_book": _DOCS[2:3], "law": _DOCS[3:4], "code": _DOCS[4:5],
    "humanities": _DOCS[5:6], "custom": _DOCS, "notes": [("Kindle 读书笔记 · 2 本书", "kindle_clippings.txt")],
}

# 粗略估算用：每页（每组划线）大约的 token 数和秒数，给用户一个量级概念
TOKENS_PER_PAGE = 4000
SECONDS_PER_PAGE = 12
OCR_SECONDS_PER_PAGE = 3


def form_label(text: str) -> QLabel:
    """表单左侧的标签：统一宽度，几组选项上下对齐。"""
    lbl = QLabel(text)
    lbl.setMinimumWidth(110)
    return lbl


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


class PasteDialog(QDialog):
    """没有文件时，直接粘贴一段文字（文章，或从 RIDI、微信读书等网页复制的笔记）。"""

    def __init__(self, parent=None, notes: bool = False):
        super().__init__(parent)
        self.setWindowTitle("粘贴读书笔记" if notes else "粘贴文字")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(label("把划线笔记粘贴到下面（每条之间空一行；日期时间行会自动去掉）：" if notes else
                               "把要做成卡片的文字粘贴到下面：", "Big", wrap=True))
        self.title = QLineEdit()
        self.title.setPlaceholderText("书名（建议填写，会显示在卡片背面）")
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


class SourcePage(QWidget):
    back_requested = Signal()
    start_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.group: TemplateGroup | None = None
        self.path: Path | None = None
        self.info: PdfInfo | None = None
        self.book_title: str | None = None  # 粘贴文字时用户填的书名
        self._getters: dict[str, Callable[[], object]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 24)
        layout.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("第二步：选择资料和选项", "PageTitle"))
        head.addSpacing(16)
        self.purpose_label = label("", "Big")
        head.addWidget(self.purpose_label)
        change = QPushButton("换一个用途")
        change.setObjectName("Link")
        change.clicked.connect(self.back_requested)
        head.addWidget(change)
        head.addStretch()
        layout.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)
        layout.addLayout(body, 1)

        # ---------------- 左边：资料
        left = QFrame()
        left.setObjectName("Panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(20, 16, 20, 16)
        left_layout.setSpacing(10)
        left_layout.addWidget(label("资料", "Big"))
        self.drop = DropZone(min_height=220)
        self.drop.file_chosen.connect(self.set_file)
        self.drop.browse_clicked.connect(self.browse)
        left_layout.addWidget(self.drop, 1)

        self.file_panel = QFrame()
        file_row = QHBoxLayout(self.file_panel)
        file_row.setContentsMargins(0, 8, 0, 8)
        icon = label("📄")
        icon.setStyleSheet("font-size: 36px;")
        file_row.addWidget(icon)
        info_col = QVBoxLayout()
        self.file_name = label("", "Big", wrap=True)
        self.file_detail = label("", "Hint", wrap=True)
        info_col.addWidget(self.file_name)
        info_col.addWidget(self.file_detail)
        file_row.addLayout(info_col, 1)
        change_file = QPushButton("换一个")
        change_file.clicked.connect(self.clear_file)
        file_row.addWidget(change_file)
        self.file_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.file_panel.hide()
        left_layout.addWidget(self.file_panel)

        self.book_row = QWidget()
        book_layout = QHBoxLayout(self.book_row)
        book_layout.setContentsMargins(0, 0, 0, 0)
        book_layout.addWidget(label("只做这本书："))
        self.book_combo = QComboBox()
        self.book_combo.currentIndexChanged.connect(self._update_estimate)
        book_layout.addWidget(self.book_combo, 1)
        self.book_row.hide()
        left_layout.addWidget(self.book_row)
        left_layout.addStretch()  # 拖放区隐藏后，文件信息靠上，空白留在下面

        links = QHBoxLayout()
        self.paste_button = QPushButton("粘贴文字…")
        self.paste_button.setObjectName("Link")
        self.paste_button.clicked.connect(self.paste_text)
        links.addWidget(self.paste_button)
        self.demo_button = QPushButton("用示例文件试试 ▾")
        self.demo_button.setObjectName("Link")
        self.demo_menu = QMenu(self.demo_button)
        self.demo_button.setMenu(self.demo_menu)
        links.addWidget(self.demo_button)
        links.addStretch()
        left_layout.addLayout(links)
        body.addWidget(left, 5)

        # ---------------- 右边：选项
        right = QFrame()
        right.setObjectName("Panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 16, 20, 16)
        right_layout.setSpacing(10)
        right_layout.addWidget(label("选项", "Big"))
        self.variant_row = QWidget()
        variant_layout = QHBoxLayout(self.variant_row)
        variant_layout.setContentsMargins(0, 0, 0, 0)
        variant_layout.addWidget(form_label("卡片类型"))
        self.variant_group = QButtonGroup(self)
        self.variant_group.idToggled.connect(lambda *_: self._rebuild_params())
        self.variant_layout = variant_layout
        right_layout.addWidget(self.variant_row)
        self.variant_hint = label("", "Hint", wrap=True)
        right_layout.addWidget(self.variant_hint)

        self.param_form = QFormLayout()
        self.param_form.setVerticalSpacing(10)
        right_layout.addLayout(self.param_form)

        common = QFormLayout()
        common.setVerticalSpacing(10)
        self.page_row = QWidget()
        page_layout = QHBoxLayout(self.page_row)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_mode = QComboBox()
        self.page_mode.addItems(["全部页面", "指定范围"])
        self.page_from, self.page_to = QSpinBox(), QSpinBox()
        self.to_label = label("到")
        for widget in (self.page_mode, self.page_from, self.to_label, self.page_to):
            page_layout.addWidget(widget)
        page_layout.addStretch()
        self.page_label = form_label("处理哪些页")
        common.addRow(self.page_label, self.page_row)
        self.max_cards = QSpinBox()
        self.max_cards.setRange(0, 5000)
        self.max_cards.setSpecialValueText("不限")
        self.max_cards.setSuffix(" 张")
        common.addRow(form_label("最多生成"), self.max_cards)
        right_layout.addLayout(common)
        self.estimate = label("", "Hint", wrap=True)
        right_layout.addWidget(self.estimate)
        right_layout.addStretch()
        body.addWidget(right, 4)

        bottom = QHBoxLayout()
        back = QPushButton("←  上一步")
        back.clicked.connect(self.back_requested)
        bottom.addWidget(back)
        bottom.addStretch()
        self.start_button = QPushButton("开始生成")
        self.start_button.setObjectName("Primary")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_requested)
        bottom.addWidget(self.start_button)
        layout.addLayout(bottom)

        self.page_mode.currentIndexChanged.connect(self._update_pages)
        for spin in (self.page_from, self.page_to):
            spin.valueChanged.connect(self._update_estimate)

    # ------------------------------------------------------------------ 用途
    @property
    def is_notes(self) -> bool:
        return bool(self.group) and self.group.variants[0].source == "notes"

    def set_group(self, group: TemplateGroup) -> None:
        was_notes = self.is_notes
        self.group = group
        self.purpose_label.setText(f"{group.icon} {group.name}")
        for button in self.variant_group.buttons():  # 卡片类型（填空卡 / 选择题）
            self.variant_group.removeButton(button)
            self.variant_layout.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        for i, tpl in enumerate(group.variants):
            radio = QRadioButton(tpl.variant)
            self.variant_group.addButton(radio, i)
            self.variant_layout.insertWidget(self.variant_layout.count(), radio)
        self.variant_group.button(0).setChecked(True)
        self.variant_row.setVisible(len(group.variants) > 1)

        if self.is_notes:
            self.drop.title.setText("把读书笔记拖到这里")
            self.drop.hint.setText("Kindle 的 My Clippings.txt，或保存成 .txt 的划线笔记；也可以直接粘贴")
            self.paste_button.setText("粘贴读书笔记…")
        else:
            self.drop.title.setText("把 PDF 或文本文件拖到这里")
            self.drop.hint.setText("支持文字版和扫描版 PDF，自动识别中文、韩文、英文")
            self.paste_button.setText("粘贴文字…")
        self.demo_menu.clear()
        for name, file in DEMO_FILES.get(group.id, _DOCS):
            if (DEMO_DIR / file).exists():
                self.demo_menu.addAction(name, lambda p=DEMO_DIR / file: self.set_file(p))
        self.demo_button.setVisible(not self.demo_menu.isEmpty())
        if was_notes != self.is_notes:  # 资料类型变了（文档 ↔ 读书笔记），原来选的文件不再适用
            self.clear_file()
        self._rebuild_params()

    def template(self) -> Template:
        return self.group.variants[max(0, self.variant_group.checkedId())]

    def _rebuild_params(self) -> None:
        if not self.group:
            return
        template = self.template()
        self.variant_hint.setText({"cloze": "填空卡：把关键内容挖空，翻面看答案。",
                                   "mcq": "选择题：四选一，翻面看正确答案和解析。"}.get(template.note_type, ""))
        self.variant_hint.setVisible(len(self.group.variants) > 1)
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self._getters = {}
        defaults = {"meaning_language": load_settings().meaning_language}
        for name, spec in template.params.items():
            if name in defaults:
                spec = spec.model_copy(update={"default": defaults[name]})
            widget, getter = make_param_widget(spec)
            widget.setToolTip(spec.description)
            self.param_form.addRow(form_label(spec.label), widget)
            self._getters[name] = getter
        self._update_pages()

    # ------------------------------------------------------------------ 资料
    def browse(self) -> None:
        filters = "读书笔记 (*.txt *.md)" if self.is_notes else "PDF 或文本文件 (*.pdf *.txt *.md)"
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", str(Path.home()), filters)
        if path:
            self.set_file(Path(path))

    def paste_text(self) -> None:
        dialog = PasteDialog(self, notes=self.is_notes)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.save_to_file()
            if path:
                self.set_file(path, title=dialog.title.text().strip() or None)

    def set_file(self, path: Path, title: str | None = None) -> None:
        self.book_row.hide()
        self.book_combo.clear()
        try:
            if self.is_notes:
                fmt, highlights = parse_notes(path.read_text(encoding="utf-8-sig"))
                if not highlights:
                    raise ValueError("没有找到划线内容")
                books = books_in(highlights)
                detail = f"识别为 {FORMAT_NAMES[fmt]}，共 {len(highlights)} 条划线"
                if len(books) > 1:
                    detail += f"，来自 {len(books)} 本书"
                    for name, count in books:
                        self.book_combo.addItem(f"{name}（{count} 条）", name)
                    self.book_row.show()
                self.info = PdfInfo(page_count=math.ceil(len(highlights) / PER_PAGE),
                                    title=books[0][0] if books else path.stem, kind="笔记")
                self._notes_counts = {name: count for name, count in books} or {None: len(highlights)}
            elif path.suffix.lower() == ".pdf":
                self.info = inspect_pdf(path)
                kind = {"文字版": "文字版", "扫描版": "扫描版（会自动识别文字，稍慢一些）",
                        "混合": "部分页面是扫描的（会自动识别）"}[self.info.kind]
                detail = f"共 {self.info.page_count} 页 · {kind}"
            else:
                text = path.read_text(encoding="utf-8-sig")
                self.info = PdfInfo(page_count=1, title=path.stem, kind="文本")
                detail = f"文本 · {len(text):,} 字"
        except Exception as exc:  # 损坏的 PDF、编码不对的文本、没内容的笔记等
            QMessageBox.warning(self, "打不开这个文件", f"{path.name}\n\n{exc}")
            return
        self.path, self.book_title = path, title
        self.file_name.setText(title or path.name)
        self.file_detail.setText(detail)
        self.drop.hide()
        self.file_panel.show()
        self.start_button.setEnabled(True)
        for spin in (self.page_from, self.page_to):
            spin.setRange(1, max(1, self.info.page_count))
        self.page_from.setValue(1)
        self.page_to.setValue(self.info.page_count)
        self.page_mode.setCurrentIndex(0)
        self._update_pages()

    def clear_file(self) -> None:
        self.path, self.info, self.book_title = None, None, None
        self.file_panel.hide()
        self.book_row.hide()
        self.drop.show()
        self.start_button.setEnabled(False)
        self._update_estimate()

    # ------------------------------------------------------------------ 选项
    def selected_pages(self) -> list[int] | None:
        if self.is_notes or self.page_mode.currentIndex() == 0:
            return None
        start, end = sorted((self.page_from.value(), self.page_to.value()))
        return list(range(start, end + 1))

    def collect(self) -> tuple[Template, dict, GenerateOptions]:
        template = self.template()
        params = {name: getter() for name, getter in self._getters.items()}
        settings = load_settings()
        options = GenerateOptions(pages=self.selected_pages(), max_cards=self.max_cards.value(),
                                  ocr_langs=settings.ocr.langs, dpi=settings.ocr.dpi,
                                  concurrency=settings.concurrency, source=template.source,
                                  notes_book=self.book_combo.currentData() if self.book_row.isVisible() else None,
                                  book_title=self.book_title)
        return template, params, options

    def _update_pages(self) -> None:
        documents = not self.is_notes
        self.page_label.setVisible(documents)
        self.page_row.setVisible(documents)
        custom = self.page_mode.currentIndex() == 1
        for widget in (self.page_from, self.to_label, self.page_to):
            widget.setVisible(custom)
        self._update_estimate()

    def _update_estimate(self) -> None:
        if not self.info:
            self.estimate.setText("选好资料后，这里会显示预计用时和 token 用量。")
            return
        if self.is_notes:
            book = self.book_combo.currentData() if self.book_row.isVisible() else None
            count = getattr(self, "_notes_counts", {}).get(book, 0) if book else sum(getattr(self, "_notes_counts", {}).values())
            n = max(1, math.ceil(count / PER_PAGE))
            unit = f"{count} 条划线（分 {n} 组处理）"
        else:
            pages = self.selected_pages()
            n = len(pages) if pages else self.info.page_count
            unit = f"{n} 页"
        concurrency = max(1, load_settings().concurrency)
        seconds = math.ceil(n / concurrency) * SECONDS_PER_PAGE + (n * OCR_SECONDS_PER_PAGE if self.info.kind in ("扫描版", "混合") else 0)
        minutes = max(1, round(seconds / 60))
        self.estimate.setText(f"预计处理 {unit}，大约 {minutes} 分钟，消耗约 {n * TOKENS_PER_PAGE / 10000:.1f} 万 token。"
                              "资料很长的话，建议先少选几页试试效果。")
