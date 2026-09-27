"""第四步：检查卡片。表格里可以勾选、直接编辑、删除、搜索；右边实时预览卡片正反面。"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QFileDialog, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton, QSplitter, QTableView,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..config import load_settings
from ..export.preview import write_outputs
from ..pipeline.result import Card, RunResult
from ..templates.loader import Template
from .anki_dialog import AnkiImportDialog
from .widgets import label

HIDDEN_FIELDS = {"surface"}  # 内部用的字段，不在表格里显示
READ_ONLY_FIELDS = {"kind"}
KIND_TEXT = {"word": "单词", "phrase": "短语/句型"}
STATUS_COLORS = {"已导入": "#16a34a", "已存在": "#98a2b3", "失败": "#dc2626"}
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

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cards)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns) + 1 + int(self.show_status)

    def is_status_column(self, column: int) -> bool:
        return self.show_status and column == len(self.columns) + 1

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
            return "导入"
        if self.is_status_column(section):
            return "Anki"
        name = self.field_at(section)
        return self.template.fields[name].label or name

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        card = self.cards[index.row()]
        if index.column() == 0:
            if role == Qt.ItemDataRole.CheckStateRole:
                return Qt.CheckState.Checked if card.selected else Qt.CheckState.Unchecked
            return None
        if self.is_status_column(index.column()):
            if role == Qt.ItemDataRole.DisplayRole:
                return card.anki_status
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(STATUS_COLORS.get(card.anki_status, "#1f2328"))
            return None
        name = self.field_at(index.column())
        value = str(card.data.get(name, "") or "")
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return KIND_TEXT.get(value, value) if name == "kind" else value
        if role == Qt.ItemDataRole.EditRole:
            return value
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        card = self.cards[index.row()]
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            card.selected = Qt.CheckState(value) == Qt.CheckState.Checked
        elif index.column() > 0 and not self.is_status_column(index.column()) and role == Qt.ItemDataRole.EditRole:
            card.data[self.field_at(index.column())] = str(value).strip()
            card.fields = self.template.render_card_fields(card.data, card.context)
        else:
            return False
        self.dataChanged.emit(index, index, [role])
        return True

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        if self.is_status_column(index.column()):
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


def preview_html(card: Card, reverse: bool) -> str:
    """卡片预览（Qt 富文本）。字段内容已经过转义，只含 <b> 等简单标记。"""
    f = card.fields
    if "Front" not in f or "Meaning" not in f:  # 不是单词卡：直接列出所有字段
        rows = "".join(f"<tr><td style='color:#667085'>{k}</td><td>{v}</td></tr>" for k, v in f.items())
        return f"<table cellpadding='6' width='100%'>{rows}</table>"
    parts = [
        "<p style='color:#98a2b3; font-size:12px;'>正面</p>",
        f"<p align='center' style='font-size:28px; font-weight:600;'>{f['Meaning'] if reverse else f['Front']}</p>",
        "<hr>",
        "<p style='color:#98a2b3; font-size:12px;'>背面</p>",
        f"<p align='center' style='font-size:22px; font-weight:600;'>{f['Front']}</p>",
    ]

    def section(title: str, body: str, translation: str) -> None:
        parts.append(f"<p style='color:#667085; font-size:13px;'>{title}</p>")
        parts.append(f"<p style='font-size:17px;'>{body}</p>")
        if translation:
            parts.append(f"<p style='color:#667085; font-size:15px;'>{translation}</p>")

    if f.get("SourceSentence"):
        section("📖 原文", f["SourceSentence"], f.get("SourceTranslation", ""))
    if f.get("Example"):
        section(f.get("ExampleLabel") or "例句", f["Example"], f.get("ExampleTranslation", ""))
    parts.append("<table width='100%' cellpadding='10' bgcolor='#f4f4f4'><tr><td>"
                 f"<span style='font-weight:600;'>单词意思：</span>{f['Meaning']}</td></tr></table>")
    if f.get("Source"):
        parts.append(f"<p style='color:#98a2b3; font-size:12px;'>{f['Source']}</p>")
    return "".join(parts)


class ReviewPage(QWidget):
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.result: RunResult | None = None
        self.model: CardTableModel | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(12)
        layout.addWidget(label("第四步：检查卡片，然后导入 Anki", "PageTitle"))
        self.summary = label("", "Hint", wrap=True)
        layout.addWidget(self.summary)

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索单词、释义、例句…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        toolbar.addWidget(self.search)
        for text, slot in (("全选", lambda: self._select_all(True)), ("全不选", lambda: self._select_all(False)),
                           ("删除选中的行", self._delete_rows)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
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
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._update_preview())

        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        direction = QHBoxLayout()
        direction.addWidget(label("卡片预览", "Big"))
        direction.addStretch()
        self.forward = QRadioButton("外语→释义")
        self.reverse = QRadioButton("释义→外语")
        self.forward.setChecked(True)
        group = QButtonGroup(self)
        for button in (self.forward, self.reverse):
            group.addButton(button)
            button.toggled.connect(lambda *_: self._update_preview())
            direction.addWidget(button)
        preview_layout.addLayout(direction)
        self.preview = QTextBrowser()
        self.preview.document().setDefaultStyleSheet("b { color: #2563eb; }")
        preview_layout.addWidget(self.preview, 1)

        splitter = QSplitter()
        splitter.addWidget(self.table)
        splitter.addWidget(preview_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        restart = QPushButton("↺  再做一份")
        restart.clicked.connect(self.restart_requested)
        bottom.addWidget(restart)
        bottom.addStretch()
        self.export_button = QPushButton("导出文件…")
        self.export_button.clicked.connect(self._export)
        bottom.addWidget(self.export_button)
        self.anki_button = QPushButton("导入 Anki")
        self.anki_button.setObjectName("Primary")
        self.anki_button.clicked.connect(self._import_to_anki)
        bottom.addWidget(self.anki_button)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    def set_result(self, result: RunResult, template: Template) -> None:
        self.result, self.template = result, template
        self.model = CardTableModel(result.cards, template, self)
        self.model.dataChanged.connect(lambda *_: (self._update_count(), self._update_preview()))
        self.model.rowsRemoved.connect(lambda *_: (self._update_count(), self._update_preview()))
        self.proxy.setSourceModel(self.model)
        self.search.clear()

        self._resize_columns()

        u = result.usage
        note = "（已取消，下面是取消前生成的卡片）" if result.cancelled else ""
        dropped = "，".join(f"{reason} {n}" for reason, n in result.dropped.items())
        self.summary.setText(f"《{result.book_title}》 · {result.template_name}{note}\n"
                             f"共 {len(result.cards)} 张卡片 · 用了 {u.total_tokens:,} 个 token · 耗时 {result.elapsed:.0f} 秒"
                             + (f" · 已自动过滤：{dropped}" if dropped else ""))
        self._update_count()
        if result.cards:
            self.table.selectRow(0)
        self._update_preview()

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
        self.preview.setHtml(preview_html(card, self.reverse.isChecked()) if card else
                             "<p style='color:#98a2b3'>在左边点一张卡片，这里显示它的样子。</p>")

    def _resize_columns(self) -> None:
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 56)
        for col in range(1, self.model.columnCount()):
            wide = not self.model.is_status_column(col) and self.model.field_at(col) in WIDE_FIELDS
            self.table.setColumnWidth(col, 260 if wide else 120)

    def _update_count(self) -> None:
        if self.model:
            selected = self.model.selected_count()
            self.count_label.setText(f"已勾选 {selected} / {len(self.model.cards)} 张")
            self.export_button.setEnabled(selected > 0)
            self.anki_button.setEnabled(selected > 0)

    def _import_to_anki(self) -> None:
        cards = self.selected_cards()
        if not cards or not self.result:
            return
        dialog = AnkiImportDialog(self, cards, self.template, self.result.book_title, load_settings())
        dialog.exec()
        if dialog.report:
            self.model.refresh_status()
            self._resize_columns()
            self.table.selectRow(0)
            self.summary.setText(f"{self.summary.text()}\nAnki：{dialog.report.summary()}")

    def _select_all(self, selected: bool) -> None:
        if self.model:
            self.model.set_all_selected(selected)

    def _delete_rows(self) -> None:
        if not self.model:
            return
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        if not rows:
            QMessageBox.information(self, "删除", "先在表格里点选要删除的行（可按住 ⌘ 多选）。")
            return
        answer = QMessageBox.question(self, "删除", f"确定删除选中的 {len(rows)} 张卡片吗？")
        if answer == QMessageBox.StandardButton.Yes:
            self.model.remove_rows(rows)

    def _export(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择保存位置", str(Path.home() / "Desktop"))
        if not folder or not self.result:
            return
        out_dir = write_outputs(dataclasses.replace(self.result, cards=self.selected_cards()), Path(folder))
        box = QMessageBox(self)
        box.setWindowTitle("已导出")
        box.setText(f"已保存 {len(self.selected_cards())} 张卡片到：\n{out_dir}")
        open_button = box.addButton("打开预览网页", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("好", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir / "cards.html")))
