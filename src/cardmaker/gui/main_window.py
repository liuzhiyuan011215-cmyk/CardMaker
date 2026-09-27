"""主窗口：四步向导（选文件 → 选用途 → 生成 → 检查并导入）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ..config import load_settings
from ..pipeline.result import RunResult
from .import_page import ImportPage
from .options_page import OptionsPage
from .progress_page import ProgressPage
from .review_page import ReviewPage
from .settings_dialog import SettingsDialog
from .widgets import StepIndicator, label
from .worker import GenerateWorker

STEPS = ["选择文件", "选择用途", "生成卡片", "检查并导入"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CardMaker · 一键把 PDF 变成 Anki 卡片")
        self.resize(1200, 820)
        self.setMinimumSize(1000, 700)
        self.worker: GenerateWorker | None = None
        self.template = None

        central = QWidget()
        central.setObjectName("Page")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("Header")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head = QVBoxLayout(header)
        head.setContentsMargins(40, 18, 40, 14)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("🗂️ CardMaker", "AppTitle"))
        titles.addWidget(label("把 PDF、单词书、读书笔记一键变成 Anki 卡片", "AppSubtitle"))
        top.addLayout(titles)
        top.addStretch()
        settings_button = QPushButton("⚙  设置")
        settings_button.clicked.connect(lambda: self.open_settings())
        top.addWidget(settings_button, alignment=Qt.AlignmentFlag.AlignTop)
        head.addLayout(top)
        self.steps = StepIndicator(STEPS)
        head.addSpacing(8)
        head.addWidget(self.steps)
        root.addWidget(header)

        self.stack = QStackedWidget()
        self.import_page = ImportPage()
        self.options_page = OptionsPage()
        self.progress_page = ProgressPage()
        self.review_page = ReviewPage()
        for page in (self.import_page, self.options_page, self.progress_page, self.review_page):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.import_page.next_requested.connect(self.to_options)
        self.options_page.back_requested.connect(lambda: self.go(0))
        self.options_page.start_requested.connect(self.start_generation)
        self.progress_page.cancel_requested.connect(self.cancel_generation)
        self.review_page.restart_requested.connect(self.restart)

    # ------------------------------------------------------------------ 导航
    def go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.steps.set_current(index)

    def to_options(self) -> None:
        self.options_page.set_document(self.import_page.path, self.import_page.info)
        self.go(1)

    def restart(self) -> None:
        self.import_page.clear()
        self.go(0)

    def open_settings(self, message: str = "") -> bool:
        return SettingsDialog(self, message).exec() == SettingsDialog.DialogCode.Accepted

    # ------------------------------------------------------------------ 生成
    def start_generation(self) -> None:
        settings = load_settings()
        if not settings.llm.api_key:
            if not self.open_settings("第一次使用，请先粘贴大模型的 API Key。"):
                return
            settings = load_settings()
            if not settings.llm.api_key:
                return
        template, params, options = self.options_page.collect()
        self.template = template
        self.worker = GenerateWorker(self.import_page.path, template, params, options, settings)
        self.worker.progress.connect(self.progress_page.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.progress_page.start()
        self.go(2)
        self.worker.start()

    def cancel_generation(self) -> None:
        if self.worker:
            self.worker.cancel()

    def on_finished(self, result: RunResult) -> None:
        self.progress_page.stop()
        self.worker = None
        if not result.cards:
            failed = [c.error for c in result.chunks if c.status == "failed"]
            reason = ("已取消。" if result.cancelled else
                      f"所有页面都失败了：{failed[0]}" if failed else
                      "没有找到合适的内容。可以换个模板，或者把水平调低一点再试。")
            QMessageBox.information(self, "没有生成卡片", reason)
            self.go(1)
            return
        self.review_page.set_result(result, self.template)
        self.go(3)

    def on_failed(self, message: str) -> None:
        self.progress_page.stop()
        self.worker = None
        QMessageBox.warning(self, "生成失败", message)
        self.go(1)
        if "Key" in message:
            self.open_settings(message)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.is_running():
            answer = QMessageBox.question(self, "正在生成", "卡片还在生成中，确定要退出吗？")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
        event.accept()
