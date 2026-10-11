"""第四步：检查卡片。表格里可以勾选、直接编辑、删除、搜索；右边实时预览卡片正反面。"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QPushButton, QSplitter, QTableView,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..config import load_settings
from ..export.preview import write_outputs
from ..i18n import tr
from ..pipeline.meaning_check import CORRECTED, ERROR, LABELS as CHECK_LABELS, NOT_FOUND, UNMATCHED, check_status
from ..pipeline.postprocess import CLOZE, add_mcq_options
from ..pipeline.result import Card, RunResult, money
from ..storage import TaskStore
from ..templates.loader import Template
from . import icons
from .anki_dialog import AnkiImportDialog
from .theme import theme
from .widgets import Dialog, button, label, segmented

HIDDEN_FIELDS = {"surface", "option_a", "option_b", "option_c", "option_d", "answer"}  # 内部用的字段，不在表格里显示
READ_ONLY_FIELDS = {"kind"}
KIND_TEXT = {"word": "单词", "phrase": "短语/句型"}
STATUS_COLORS = {"已导入": "#16a34a", "已存在": "#98a2b3", "失败": "#dc2626"}
CHECK_COLORS = {CORRECTED: "#b45309", UNMATCHED: "#dc2626", NOT_FOUND: "#b45309", ERROR: "#98a2b3"}


def check_tooltip(check: dict) -> str:
    """「词典」列的悬停说明：词典里的义项、原来的释义、提示。"""
    lines = []
    if check.get("sense"):
        lines.append(f"{check.get('source', '词典')}：{check['sense']}")
    if check.get("original"):
        lines.append(f"原来的释义：{check['original']}")
    if check.get("note"):
        lines.append(check["note"])
    return "\n".join(lines)


def cost_line(result: RunResult) -> str:
    """摘要的第三行：费用，以及平均每张卡用多少 token、花多少钱。"""
    per_card = f"{result.usage.total_tokens / len(result.cards):,.0f}"
    cost = result.cost
    if result.options.get("local_model"):
        return f"本地模型，不花钱（平均每张 {per_card} token）"
    if cost is None:
        return f"平均每张 {per_card} token（在「设置」里填上单价就能算出费用）"
    return f"费用约 {money(cost)} 元（平均每张 {per_card} token，约 {money(cost / len(result.cards))} 元）"


WIDE_FIELDS = {"source_sentence", "source_translation", "example", "example_translation", "book_example",
               "book_example_translation", "ai_example", "ai_example_translation"}


class CardTableModel(QAbstractTableModel):
    """表格数据：第 0 列是勾选框，中间是模板输出的字段，导入 Anki 后最后多一列「Anki」显示结果。

    编辑字段后自动重新生成卡片内容。
    """

    def __init__(self, cards: list[Card], template: Template, parent=None):
        super().__init__(parent)
        self.cards = cards
        self.template = template
        # 只显示至少一张卡片有内容的字段（比如没勾「AI 例句」就不显示那一列）
        self.columns = [name for name in template.fields
                        if name not in HIDDEN_FIELDS and any(c.data.get(name) for c in cards)]
        self.show_status = any(c.anki_status for c in cards)
        self.show_dict = any(check_status(c) for c in cards)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cards)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns) + 1 + int(self.show_dict) + int(self.show_status)

    def is_dict_column(self, column: int) -> bool:
        return self.show_dict and column == len(self.columns) + 1

    def is_status_column(self, column: int) -> bool:
        return self.show_status and column == len(self.columns) + 1 + int(self.show_dict)

    def refresh_status(self) -> None:
        """导入 Anki 之后调用：显示「Anki」结果列。"""
        self.beginResetModel()
        self.show_status = any(c.anki_status for c in self.cards)
        self.endResetModel()

    def field_at(self, column: int) -> str:
        return self.columns[column - 1]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        if section == 0:
            return tr("导入")
        if self.is_status_column(section):
            return "Anki"
        if self.is_dict_column(section):
            return tr("词典")
        name = self.field_at(section)
        return tr(self.template.fields[name].label or name)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        card = self.cards[index.row()]
        if index.column() == 0:
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if card.selected else Qt.CheckState.Unchecked
            return None
        if self.is_dict_column(index.column()):
            check = card.context.get("dict_check") or {}
            if role == Qt.ItemDataRole.DisplayRole:
                return tr(CHECK_LABELS.get(check.get("status"), ""))
            if role == Qt.ItemDataRole.ToolTipRole:
                return tr(check_tooltip(check))
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(CHECK_COLORS.get(check.get("status"), "#1f2328"))
            return None
        if self.is_status_column(index.column()):
            if role == Qt.ItemDataRole.DisplayRole:
                return tr(card.anki_status)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(STATUS_COLORS.get(card.anki_status, "#1f2328"))
            return None
        name = self.field_at(index.column())
        raw = card.data.get(name, "")
        value = "；".join(map(str, raw)) if isinstance(raw, list) else str(raw or "")
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return tr(KIND_TEXT.get(value, value)) if name == "kind" else value
        if role == Qt.ItemDataRole.EditRole:
            return value
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        card = self.cards[index.row()]
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            card.selected = Qt.CheckState(value) == Qt.CheckState.Checked
        elif (index.column() > 0 and not self.is_status_column(index.column())
              and not self.is_dict_column(index.column()) and role == Qt.ItemDataRole.EditRole):
            name = self.field_at(index.column())
            if self.template.fields[name].type == "list":
                card.data[name] = [part.strip() for part in str(value).replace(";", "；").split("；") if part.strip()]
            else:
                card.data[name] = str(value).strip()
            if self.template.is_mcq:  # 选择题：正确答案或干扰项改了，重新排 A–D
                card.data = add_mcq_options(card.data)
            card.fields = self.template.render_card_fields(card.data, card.context)
        else:
            return False
        self.dataChanged.emit(index, index, [role])
        return True

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        if self.is_status_column(index.column()) or self.is_dict_column(index.column()):
            return flags
        if self.field_at(index.column()) not in READ_ONLY_FIELDS:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def remove_rows(self, rows: set[int]) -> None:
        for row in sorted(rows, reverse=True):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.cards[row]
            self.endRemoveRows()

    def set_all_selected(self, selected: bool) -> None:
        for card in self.cards:
            card.selected = selected
        if self.cards:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.cards) - 1, 0),
                                  [Qt.ItemDataRole.CheckStateRole])

    def selected_count(self) -> int:
        return sum(1 for c in self.cards if c.selected)


def _cloze(text: str, reveal: bool) -> str:
    """填空卡预览：正面把 {{c1::答案::提示}} 显示成 [提示] 或 [……]，背面显示答案。"""
    def repl(match):
        answer, hint = match.group(1), match.group(2)
        if reveal:
            return f"<b>{answer}</b>"
        return f"<span style='color:{theme.color('highlight')}; font-weight:600;'>[{hint or '……'}]</span>"
    return CLOZE.sub(repl, text)


def _sections(f: dict, pairs: list[tuple[str, str]]) -> str:
    html = ""
    for title, key in pairs:
        if f.get(key):
            html += f"<p style='color:{theme.color('text2')}; font-size:13px;'>{tr(title)}</p><p style='font-size:16px;'>{f[key]}</p>"
    if f.get("Source"):
        html += f"<p style='color:{theme.color('text3')}; font-size:12px;'>{f['Source']}</p>"
    return html


def preview_html(card: Card, reverse: bool = False, note_type: str = "vocab") -> str:
    """卡片预览（Qt 富文本）。字段内容已经过转义，只含 <b>、<pre> 等简单标记。"""
    f = card.fields
    front_label = f"<p style='color:{theme.color('text3')}; font-size:12px;'>{tr('正面')}</p>"
    back_label = (f"<hr style='background-color:{theme.color('border')}'>"  # 正反面之间一条浅灰线
                  f"<p style='color:{theme.color('text3')}; font-size:12px;'>{tr('背面')}</p>")
    if note_type == "cloze":
        return (front_label + f"<p style='font-size:19px;'>{_cloze(f.get('Text', ''), False)}</p>" + back_label
                + f"<p style='font-size:19px;'>{_cloze(f.get('Text', ''), True)}</p>"
                + _sections(f, [("要点", "Extra"), ("原文", "SourceText")]))
    if note_type == "mcq":
        def options(answer: str | None) -> str:
            rows = ""
            for letter in "ABCD":
                color = " bgcolor='#dcfce7'" if letter == answer else ""
                rows += f"<tr><td{color} width='30'><b>{letter}</b></td><td{color}>{f.get(letter, '')}</td></tr>"
            return f"<table width='100%' cellpadding='8' cellspacing='4'>{rows}</table>"
        question = f"<p style='font-size:18px; font-weight:600;'>{f.get('Question', '')}</p>"
        return (front_label + question + options(None) + back_label + question + options(f.get("Answer"))
                + f"<p>{tr('正确答案：')}<b>{f.get('Answer', '')}</b></p>"
                + _sections(f, [("解析", "Explanation"), ("原文", "SourceText")]))
    if "Front" not in f or "Meaning" not in f:  # 其他笔记类型：直接列出所有字段
        rows = "".join(f"<tr><td style='color:{theme.color('text2')}'>{k}</td><td>{v}</td></tr>" for k, v in f.items())
        return f"<table cellpadding='6' width='100%'>{rows}</table>"
    parts = [
        front_label,
        f"<p align='center' style='font-size:28px; font-weight:600;'>{f['Meaning'] if reverse else f['Front']}</p>",
        back_label,
        f"<p align='center' style='font-size:22px; font-weight:600;'>{f['Front']}</p>",
    ]

    def section(title: str, body: str, translation: str) -> None:
        parts.append(f"<p style='color:{theme.color('text2')}; font-size:13px;'>{tr(title)}</p>")
        parts.append(f"<p style='font-size:17px;'>{body}</p>")
        if translation:
            parts.append(f"<p style='color:{theme.color('text2')}; font-size:15px;'>{translation}</p>")

    if f.get("SourceSentence"):
        section("原文", f["SourceSentence"], f.get("SourceTranslation", ""))
    if f.get("Example"):
        section(f.get("ExampleLabel") or "例句", f["Example"], f.get("ExampleTranslation", ""))
    parts.append(f"<table width='100%' cellpadding='10' bgcolor='{theme.color('subtle')}'><tr><td>"
                 f"<span style='font-weight:600;'>{tr('单词意思：')}</span>{f['Meaning']}</td></tr></table>")
    if f.get("Source"):
        parts.append(f"<p style='color:{theme.color('text3')}; font-size:12px;'>{f['Source']}</p>")
    return "".join(parts)


class StatsDialog(Dialog):
    """「详细统计」：第一行（书名、模板）是副标题，其余每行左边项目名、右边数字（可以选中复制）。"""

    def __init__(self, parent, lines: list[str]):
        super().__init__(parent, "详细统计", heading="详细统计", subtitle=lines[0], width=680)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(10)
        for row, line in enumerate(lines[1:]):
            sep = "：" if "：" in line else ": "  # 韩文翻译后是半角冒号
            name, _, value = line.partition(sep)
            value_label = label(value or name, wrap=True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label(name if value else "", "secondary"), row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_label, row, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)
        close = button("关闭", "primary")
        close.clicked.connect(self.accept)
        self.footer.right.addWidget(close)


class ReviewPage(QWidget):
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.result: RunResult | None = None
        self.model: CardTableModel | None = None
        self.store: TaskStore | None = None
        # 修改、勾选、删除后稍等片刻自动存进数据库（历史记录里再打开还在）
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(12)
        layout.addWidget(label("第四步：检查卡片，然后导入 Anki", "PageTitle"))
        self.summary = label("", "Hint", wrap=True)
        layout.addWidget(self.summary)

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索卡片内容…"))
        self.search.addAction(icons.icon("search", "text3"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        toolbar.addWidget(self.search)
        for text, slot in (("全选", lambda: self._select_all(True)), ("全不选", lambda: self._select_all(False)),
                           ("删除选中的行", self._delete_rows)):
            tool = button(text)
            tool.clicked.connect(slot)
            toolbar.addWidget(tool)
        toolbar.addStretch()
        self.count_label = label("", "Hint")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._update_preview())

        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        direction = QHBoxLayout()
        direction.addWidget(label("卡片预览", "Big"))
        direction.addStretch()
        self.direction_tabs, group = segmented(["外语→释义", "释义→外语"])
        self.forward, self.reverse = group.buttons()
        group.idToggled.connect(lambda *_: self._update_preview())
        direction.addWidget(self.direction_tabs)
        preview_layout.addLayout(direction)
        self.preview = QTextBrowser()
        self.preview.document().setDefaultStyleSheet(f"b {{ color: {theme.color('highlight')}; }}")
        preview_layout.addWidget(self.preview, 1)

        splitter = QSplitter()
        splitter.addWidget(self.table)
        splitter.addWidget(preview_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        restart = button("再做一份", icon="arrow_counterclockwise")
        restart.clicked.connect(self.restart_requested)
        bottom.addWidget(restart)
        stats = button("详细统计", icon="info")
        stats.clicked.connect(self._show_stats)
        bottom.addWidget(stats)
        bottom.addStretch()
        self.export_button = QPushButton(tr("导出文件…"))
        self.export_button.clicked.connect(self._export)
        bottom.addWidget(self.export_button)
        self.anki_button = QPushButton(tr("导入 Anki"))
        self.anki_button.setObjectName("Primary")
        self.anki_button.clicked.connect(self._import_to_anki)
        bottom.addWidget(self.anki_button)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    def set_result(self, result: RunResult, template: Template, store: TaskStore | None = None) -> None:
        self.result, self.template, self.store = result, template, store
        self.direction_tabs.setVisible(template.note_type == "vocab")  # 「外语→释义 / 释义→外语」只对单词卡有意义
        self.model = CardTableModel(result.cards, template, self)
        self.model.dataChanged.connect(lambda *_: (self._update_count(), self._update_preview(),
                                                   self._save_timer.start()))
        self.model.rowsRemoved.connect(lambda *_: (self._update_count(), self._update_preview(),
                                                   self._save_timer.start()))
        self.proxy.setSourceModel(self.model)
        self.search.clear()

        self._resize_columns()

        u = result.usage
        note = "（已取消，下面是取消前生成的卡片）" if result.cancelled else ""
        dropped = "，".join(f"{reason} {n}" for reason, n in result.dropped.items())
        lines = [f"《{result.book_title}》 · {result.template_name}{note}",
                 f"共 {len(result.cards)} 张卡片 · 用了 {u.total_tokens:,} 个 token · 耗时 {result.elapsed:.0f} 秒"
                 + (f" · 已自动过滤：{dropped}" if dropped else "")]
        if result.cards:
            lines.append(cost_line(result))
        self.summary.setText("\n".join(tr(line) for line in lines))
        self._update_count()
        if result.cards:
            self.table.selectRow(0)
        self._update_preview()

    def save(self) -> None:
        if self.store and self.result and self.result.task_id is not None:
            self.store.save_cards(self.result.task_id, self.result.cards)

    def selected_cards(self) -> list[Card]:
        return [c for c in (self.result.cards if self.result else []) if c.selected]

    # ------------------------------------------------------------------
    def _current_card(self) -> Card | None:
        index = self.table.currentIndex()
        if not self.model or not index.isValid():
            return None
        return self.model.cards[self.proxy.mapToSource(index).row()]

    def _update_preview(self) -> None:
        card = self._current_card()
        note_type = self.template.note_type if self.result else "vocab"
        self.preview.setHtml(preview_html(card, self.reverse.isChecked(), note_type) if card else
                             f"<p style='color:{theme.color('text3')}'>{tr('在左边点一张卡片，这里显示它的样子。')}</p>")

    def _resize_columns(self) -> None:
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 56)
        for col in range(1, self.model.columnCount()):
            extra = self.model.is_status_column(col) or self.model.is_dict_column(col)  # 「词典」「Anki」列不是字段
            wide = not extra and self.model.field_at(col) in WIDE_FIELDS
            width = 260 if wide else 120
            if extra:  # 核对结果的字有长有短（英语「From dictionary」比中文长），按内容放宽
                width = max(width, self.table.sizeHintForColumn(col), header.sectionSizeHint(col))
            self.table.setColumnWidth(col, width)
        for col in range(self.model.columnCount()):  # 先恢复原来的列顺序（换了一批卡片）
            header.moveSection(header.visualIndex(col), col)
        if self.model.show_dict and "meaning" in self.model.columns:  # 「词典」列挪到「释义」右边，一眼就能对上
            dict_col = len(self.model.columns) + 1
            header.moveSection(header.visualIndex(dict_col), self.model.columns.index("meaning") + 2)

    def _update_count(self) -> None:
        if self.model:
            selected = self.model.selected_count()
            self.count_label.setText(tr(f"已勾选 {selected} / {len(self.model.cards)} 张"))
            self.export_button.setEnabled(selected > 0)
            self.anki_button.setEnabled(selected > 0)

    def _import_to_anki(self) -> None:
        cards = self.selected_cards()
        if not cards or not self.result:
            return
        dialog = AnkiImportDialog(self, cards, self.template, self.result.book_title, load_settings())
        dialog.exec()
        if dialog.report:
            self.save()  # 记住哪些已经导入过
            self.model.refresh_status()
            self._resize_columns()
            self.table.selectRow(0)
            self.summary.setText(f"{self.summary.text()}\n{tr(f'Anki：{dialog.report.summary()}')}")

    def stats_text(self) -> str:
        """「详细统计」：页数、丢弃原因、词典核对结果、token（含词典核对）、费用、平均每张卡、耗时。"""
        return "\n".join(tr(line) for line in self.result.summary_lines()) if self.result else ""

    def _show_stats(self) -> None:
        if self.result:
            StatsDialog(self, self.stats_text().split("\n")).exec()

    def _select_all(self, selected: bool) -> None:
        if self.model:
            self.model.set_all_selected(selected)

    def _delete_rows(self) -> None:
        if not self.model:
            return
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        if not rows:
            QMessageBox.information(self, tr("删除"), tr("先在表格里点选要删除的行（可按住 ⌘ 多选）。"))
            return
        answer = QMessageBox.question(self, tr("删除"), tr(f"确定删除选中的 {len(rows)} 张卡片吗？"))
        if answer == QMessageBox.StandardButton.Yes:
            self.model.remove_rows(rows)

    def _export(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("选择保存位置"), str(Path.home() / "Desktop"))
        if not folder or not self.result:
            return
        out_dir = write_outputs(dataclasses.replace(self.result, cards=self.selected_cards()), Path(folder))
        box = QMessageBox(self)
        box.setWindowTitle(tr("已导出"))
        box.setText(tr(f"已保存 {len(self.selected_cards())} 张卡片到：\n{out_dir}"))
        open_button = box.addButton(tr("打开预览网页"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("好"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir / "cards.html")))
