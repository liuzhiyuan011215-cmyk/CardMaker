"""「导入 Anki」窗口：检测 Anki → 选牌组和方向 → 一键导入；连不上 Anki 时可以导出 .apkg 文件。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QMessageBox, QPushButton, QRadioButton, QVBoxLayout)

from ..anki import AnkiConnect, ImportOptions, ImportReport, export_apkg, import_to_anki
from ..anki.exporter import card_tags
from ..config import Settings
from ..pipeline.result import Card
from ..templates.loader import Template
from .widgets import label
from .worker import FunctionTask

ANKICONNECT_CODE = "2055492159"
DIRECTIONS = [("外语→释义（看单词，口头说意思）", True, False),
              ("释义→外语（看意思，动笔写单词）", False, True),
              ("两个方向都要（推荐）", True, True)]


class AnkiImportDialog(QDialog):
    def __init__(self, parent, cards: list[Card], template: Template, book_title: str, settings: Settings,
                 client: AnkiConnect | None = None, autostart: bool = True):
        super().__init__(parent)
        self.setWindowTitle("导入 Anki")
        self.setMinimumWidth(620)
        self.cards, self.template, self.book_title = cards, template, book_title
        self.client = client or AnkiConnect(settings.anki_url)
        self.connected = False
        self.report: ImportReport | None = None
        self._task: FunctionTask | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(label(f"把勾选的 {len(cards)} 张卡片导入 Anki", "PageTitle"))

        status_row = QHBoxLayout()
        self.status = label("正在连接 Anki…", "Hint", wrap=True)
        self.retry = QPushButton("重新检测")
        self.retry.clicked.connect(lambda: self.start_check())
        self.retry.hide()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.retry)
        layout.addLayout(status_row)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.deck = QComboBox()
        self.deck.setEditable(True)
        self.deck.setCurrentText(f"{settings.default_deck}::{book_title}")
        self.deck.setToolTip("可以选已有的牌组，也可以直接输入新名字；用 :: 表示子牌组")
        form.addRow("放进牌组", self.deck)

        direction_box = QVBoxLayout()
        self.direction_group = QButtonGroup(self)
        for i, (text, _, _) in enumerate(DIRECTIONS):
            radio = QRadioButton(text)
            self.direction_group.addButton(radio, i)
            direction_box.addWidget(radio)
        self.direction_group.button(2).setChecked(True)
        form.addRow("卡片方向", direction_box)

        self.extra_tags = QLineEdit()
        self.extra_tags.setPlaceholderText("可选，多个标签用空格隔开，例如：自学单词 期末")
        form.addRow("额外标签", self.extra_tags)
        tags = card_tags(cards[0], template.spec.tags, book_title) if cards else []
        form.addRow("自动标签", label("  ".join(tags), "Hint", wrap=True))
        layout.addLayout(form)

        self.result_label = label("", "Big", wrap=True)
        self.result_label.hide()
        layout.addWidget(self.result_label)

        buttons = QHBoxLayout()
        self.apkg_button = QPushButton("导出 .apkg 文件…")
        self.apkg_button.setToolTip("不打开 Anki 也能用：导出后双击这个文件就会导入 Anki")
        self.apkg_button.clicked.connect(self.choose_apkg_path)
        buttons.addWidget(self.apkg_button)
        buttons.addStretch()
        self.close_button = QPushButton("取消")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.browse_button = QPushButton("在 Anki 里查看")
        self.browse_button.clicked.connect(self.browse_in_anki)
        self.browse_button.hide()
        buttons.addWidget(self.browse_button)
        self.import_button = QPushButton("导入 Anki")
        self.import_button.setObjectName("Primary")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(lambda: self.start_import())
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)

        if autostart:
            self.start_check()

    # ------------------------------------------------------------------ 检测 Anki
    def start_check(self, background: bool = True) -> None:
        self.status.setText("正在连接 Anki…")
        self.retry.hide()
        self.import_button.setEnabled(False)
        self._run(self.client.deck_names, self.on_connected, self.on_unavailable, background)

    def on_connected(self, decks: list[str]) -> None:
        self.connected = True
        current = self.deck.currentText()
        self.deck.clear()
        self.deck.addItems(sorted(decks))
        self.deck.setCurrentText(current)
        self.status.setText("✅ 已连接 Anki。牌组、笔记类型会自动创建；Anki 里已有的卡片会自动跳过。")
        self.import_button.setEnabled(bool(self.cards))

    def on_unavailable(self, message: str) -> None:
        self.connected = False
        self.status.setText("⚠️ 没有连上 Anki。请先打开 Anki，并确认装了 AnkiConnect 插件"
                            f"（Anki 菜单：工具 → 插件 → 获取插件，代码 {ANKICONNECT_CODE}），然后点「重新检测」。\n"
                            "也可以先导出 .apkg 文件，以后双击它导入。")
        self.retry.show()

    # ------------------------------------------------------------------ 导入
    def options(self) -> ImportOptions:
        _, forward, reverse = DIRECTIONS[self.direction_group.checkedId()]
        return ImportOptions(deck=self.deck.currentText().strip() or "CardMaker", forward=forward, reverse=reverse,
                             tags=self.extra_tags.text().split())

    def start_import(self, background: bool = True) -> None:
        options = self.options()
        self.import_button.setEnabled(False)
        self.apkg_button.setEnabled(False)
        self.status.setText("正在导入…")
        self._run(lambda: import_to_anki(self.client, self.cards, self.template.spec.note_type,
                                         self.template.spec.tags, self.book_title, options),
                  self.on_imported, self.on_import_failed, background)

    def on_imported(self, report: ImportReport) -> None:
        self.report = report
        for card, status in zip(self.cards, report.statuses):
            card.anki_status = status
        text = f"✅ {report.summary()}。"
        if report.errors:
            text += "\n失败原因：" + "；".join(report.errors[:3])
        self.status.setText("导入完成。")
        self.result_label.setText(text)
        self.result_label.show()
        self.close_button.setText("完成")
        self.close_button.clicked.disconnect()
        self.close_button.clicked.connect(self.accept)
        self.import_button.hide()
        self.browse_button.show()

    def on_import_failed(self, message: str) -> None:
        self.status.setText(f"❌ 导入失败：{message}")
        self.import_button.setEnabled(self.connected)
        self.apkg_button.setEnabled(True)

    def browse_in_anki(self) -> None:
        if self.report:
            query = f'"deck:{self.report.deck}"'
            self._run(lambda: self.client.gui_browse(query), self._on_browse_opened, self._on_browse_failed, True)

    def _on_browse_opened(self, _result) -> None:
        self.status.setText("已在 Anki 里打开浏览窗口。")

    def _on_browse_failed(self, message: str) -> None:
        self.status.setText(f"打不开 Anki 浏览窗口：{message}")

    # ------------------------------------------------------------------ .apkg
    def choose_apkg_path(self) -> None:
        default = Path.home() / "Desktop" / f"{self.book_title}.apkg"
        path, _ = QFileDialog.getSaveFileName(self, "导出 .apkg 文件", str(default), "Anki 牌组包 (*.apkg)")
        if path:
            self.export_to(Path(path))

    def export_to(self, path: Path, notify: bool = True) -> Path:
        options = self.options()
        export_apkg(self.cards, self.template.spec.note_type, self.template.spec.tags, self.book_title, options, path)
        if notify:
            box = QMessageBox(self)
            box.setWindowTitle("已导出")
            box.setText(f"已导出 {len(self.cards)} 张卡片：\n{path}\n\n双击这个文件就会导入 Anki。")
            reveal = box.addButton("在访达中显示", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("好", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is reveal:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        return path

    # ------------------------------------------------------------------
    def _run(self, fn, on_done, on_error, background: bool) -> None:
        """后台执行 fn（界面不卡）；测试时 background=False 同步执行。

        on_done / on_error 必须是本窗口的方法（不能是 lambda），Qt 才会把它们排到界面线程执行。
        """
        self._task = FunctionTask(fn)
        self._task.finished.connect(on_done)
        self._task.failed.connect(on_error)
        if background:
            self._task.start()
        else:
            self._task.run()
