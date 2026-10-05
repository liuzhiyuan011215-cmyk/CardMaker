"""「导入 Anki」窗口：检测 Anki → 选牌组、方向、要不要发音 → 一键导入；连不上 Anki 时可以导出 .apkg 文件。"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ..anki import AnkiConnect, ImportOptions, ImportReport, export_apkg, has_directions, import_to_anki
from ..anki.exporter import card_tags
from ..config import Settings
from ..i18n import tr
from ..pipeline.result import Card
from ..templates.loader import Template
from ..tts import CachedTTS, TTSError, add_audio, make_tts
from .widgets import label
from .worker import ProgressTask

ANKICONNECT_CODE = "2055492159"
DIRECTIONS = [("外语→释义（看单词，口头说意思）", True, False),
              ("释义→外语（看意思，动笔写单词）", False, True),
              ("两个方向都要（推荐）", True, True)]


class AnkiImportDialog(QDialog):
    def __init__(self, parent, cards: list[Card], template: Template, book_title: str, settings: Settings,
                 client: AnkiConnect | None = None, autostart: bool = True,
                 tts_factory: Callable[[], CachedTTS] | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("导入 Anki"))
        self.setMinimumWidth(640)
        self.cards, self.template, self.book_title, self.settings = cards, template, book_title, settings
        self.client = client or AnkiConnect(settings.anki_url)
        self.tts_factory = tts_factory or (lambda: make_tts(settings))
        self.connected = False
        self.report: ImportReport | None = None
        self.audio_errors: list[str] = []
        self._task: ProgressTask | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(label(f"把勾选的 {len(cards)} 张卡片导入 Anki", "PageTitle"))

        status_row = QHBoxLayout()
        self.status = label("正在连接 Anki…", "Hint", wrap=True)
        self.retry = QPushButton(tr("重新检测"))
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
        self.deck.setToolTip(tr("可以选已有的牌组，也可以直接输入新名字；用 :: 表示子牌组"))
        form.addRow(tr("放进牌组"), self.deck)

        vocab = has_directions(template.note_type)
        self.direction_widget = QWidget()
        direction_box = QVBoxLayout(self.direction_widget)
        direction_box.setContentsMargins(0, 0, 0, 0)
        self.direction_group = QButtonGroup(self)
        for i, (text, _, _) in enumerate(DIRECTIONS):
            radio = QRadioButton(tr(text))
            self.direction_group.addButton(radio, i)
            direction_box.addWidget(radio)
        self.direction_group.button(2).setChecked(True)
        if vocab:
            form.addRow(tr("卡片方向"), self.direction_widget)
        else:
            self.direction_widget.hide()

        self.audio_box = QCheckBox(tr("单词和例句都配上发音（同样的内容只生成一次）"))
        self.audio_box.setChecked(settings.tts.enabled)
        if vocab:
            form.addRow(tr("发音"), self.audio_box)
        else:
            self.audio_box.hide()

        self.extra_tags = QLineEdit()
        self.extra_tags.setPlaceholderText(tr("可选，多个标签用空格隔开，例如：自学单词 期末"))
        form.addRow(tr("额外标签"), self.extra_tags)
        tags = card_tags(cards[0], template.spec.tags, book_title) if cards else []
        form.addRow(tr("自动标签"), label("  ".join(tags), "Hint", wrap=True))
        layout.addLayout(form)

        self.result_label = label("", "Big", wrap=True)
        self.result_label.hide()
        layout.addWidget(self.result_label)

        buttons = QHBoxLayout()
        self.apkg_button = QPushButton(tr("导出 .apkg 文件…"))
        self.apkg_button.setToolTip(tr("不打开 Anki 也能用：导出后双击这个文件就会导入 Anki"))
        self.apkg_button.clicked.connect(self.choose_apkg_path)
        buttons.addWidget(self.apkg_button)
        buttons.addStretch()
        self.close_button = QPushButton(tr("取消"))
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.browse_button = QPushButton(tr("在 Anki 里查看"))
        self.browse_button.clicked.connect(self.browse_in_anki)
        self.browse_button.hide()
        buttons.addWidget(self.browse_button)
        self.import_button = QPushButton(tr("导入 Anki"))
        self.import_button.setObjectName("Primary")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(lambda: self.start_import())
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)

        if autostart:
            self.start_check()

    # ------------------------------------------------------------------ 检测 Anki
    def start_check(self, background: bool = True) -> None:
        self.status.setText(tr("正在连接 Anki…"))
        self.retry.hide()
        self.import_button.setEnabled(False)
        self._run(lambda report: self.client.deck_names(), self.on_connected, self.on_unavailable, background)

    def on_connected(self, decks: list[str]) -> None:
        self.connected = True
        current = self.deck.currentText()
        self.deck.clear()
        self.deck.addItems(sorted(decks))
        self.deck.setCurrentText(current)
        self.status.setText(tr("✅ 已连接 Anki。牌组、笔记类型会自动创建；Anki 里已有的卡片会自动跳过。"))
        self.import_button.setEnabled(bool(self.cards))

    def on_unavailable(self, message: str) -> None:
        self.connected = False
        self.status.setText(tr("⚠️ 没有连上 Anki。请先打开 Anki，并确认装了 AnkiConnect 插件"
                            f"（Anki 菜单：工具 → 插件 → 获取插件，代码 {ANKICONNECT_CODE}），然后点「重新检测」。\n"
                            "也可以先导出 .apkg 文件，以后双击它导入。"))
        self.retry.show()

    # ------------------------------------------------------------------ 导入
    def options(self) -> ImportOptions:
        _, forward, reverse = DIRECTIONS[self.direction_group.checkedId()]
        return ImportOptions(deck=self.deck.currentText().strip() or "CardMaker", forward=forward, reverse=reverse,
                             tags=self.extra_tags.text().split())

    def want_audio(self) -> bool:
        return has_directions(self.template.note_type) and self.audio_box.isChecked()

    def prepare_audio(self, report: Callable[[str], None]) -> list[Path]:
        """需要时给单词卡生成发音，返回音频文件。发音失败不影响导入，只是少了声音。"""
        self.audio_errors = []
        if not self.want_audio():
            return []
        try:
            tts = self.tts_factory()
        except TTSError as exc:
            self.audio_errors = [str(exc)]
            return []
        report("正在生成发音…")
        media, self.audio_errors = add_audio(self.cards, tts, progress=lambda done, total: report(
            f"正在生成发音 {done}/{total}…"))
        return media

    def start_import(self, background: bool = True) -> None:
        options = self.options()
        self.import_button.setEnabled(False)
        self.apkg_button.setEnabled(False)
        self.status.setText(tr("正在导入…"))

        def job(report):
            media = self.prepare_audio(report)
            report("正在写入 Anki…")
            return import_to_anki(self.client, self.cards, self.template.note_type, self.template.spec.tags,
                                  self.book_title, options, media)

        self._run(job, self.on_imported, self.on_import_failed, background)

    def on_imported(self, report: ImportReport) -> None:
        self.report = report
        for card, status in zip(self.cards, report.statuses):
            card.anki_status = status
        text = f"✅ {report.summary()}。"
        if self.want_audio():
            text += "\n🔊 已配上发音。" if not self.audio_errors else f"\n🔊 有 {len(self.audio_errors)} 条发音没生成：{self.audio_errors[0]}"
        if report.errors:
            text += "\n失败原因：" + "；".join(report.errors[:3])
        self.status.setText(tr("导入完成。"))
        self.result_label.setText(tr(text))
        self.result_label.show()
        self.close_button.setText(tr("完成"))
        self.close_button.clicked.disconnect()
        self.close_button.clicked.connect(self.accept)
        self.import_button.hide()
        self.browse_button.show()

    def on_import_failed(self, message: str) -> None:
        self.status.setText(tr(f"❌ 导入失败：{message}"))
        self.import_button.setEnabled(self.connected)
        self.apkg_button.setEnabled(True)

    def on_progress(self, message: str) -> None:
        self.status.setText(tr(message))

    def browse_in_anki(self) -> None:
        if self.report:
            query = f'"deck:{self.report.deck}"'
            self._run(lambda report: self.client.gui_browse(query), self._on_browse_opened,
                      self._on_browse_failed, True)

    def _on_browse_opened(self, _result) -> None:
        self.status.setText(tr("已在 Anki 里打开浏览窗口。"))

    def _on_browse_failed(self, message: str) -> None:
        self.status.setText(tr(f"打不开 Anki 浏览窗口：{message}"))

    # ------------------------------------------------------------------ .apkg
    def choose_apkg_path(self) -> None:
        default = Path.home() / "Desktop" / f"{self.book_title}.apkg"
        path, _ = QFileDialog.getSaveFileName(self, tr("导出 .apkg 文件"), str(default), tr("Anki 牌组包 (*.apkg)"))
        if not path:
            return
        self.apkg_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self._run(lambda report: self.export_to(Path(path), report), self.on_exported, self.on_import_failed, True)

    def export_to(self, path: Path, report: Callable[[str], None] = lambda message: None) -> Path:
        media = self.prepare_audio(report)
        report("正在打包 .apkg…")
        return export_apkg(self.cards, self.template.note_type, self.template.spec.tags, self.book_title,
                           self.options(), path, media)

    def on_exported(self, path: Path) -> None:
        self.apkg_button.setEnabled(True)
        self.import_button.setEnabled(self.connected)
        self.status.setText(tr("已导出。"))
        box = QMessageBox(self)
        box.setWindowTitle(tr("已导出"))
        box.setText(tr(f"已导出 {len(self.cards)} 张卡片：\n{path}\n\n双击这个文件就会导入 Anki。"))
        reveal = box.addButton(tr("在访达中显示"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("好"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is reveal:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ------------------------------------------------------------------
    def _run(self, fn, on_done, on_error, background: bool) -> None:
        """后台执行 fn(report)（界面不卡）；测试时 background=False 同步执行。

        on_done / on_error 必须是本窗口的方法（不能是 lambda），Qt 才会把它们排到界面线程执行。
        """
        self._task = ProgressTask(fn)
        self._task.progress.connect(self.on_progress)
        self._task.finished.connect(on_done)
        self._task.failed.connect(on_error)
        if background:
            self._task.start()
        else:
            self._task.run()
