"""主窗口：四步向导（选文件 → 选用途 → 生成 → 检查并导入），外加历史记录和断点续传。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ..config import load_settings
from ..pipeline.result import RunResult
from ..pipeline.runner import load_result
from ..storage import TaskStore
from ..templates.loader import load_template
from .history_dialog import HistoryDialog
from .import_page import ImportPage
from .options_page import OptionsPage
from .progress_page import ProgressPage
from .review_page import ReviewPage
from .settings_dialog import SettingsDialog
from .widgets import StepIndicator, label
from .worker import GenerateWorker

STEPS = ["选择文件", "选择用途", "生成卡片", "检查并导入"]


class MainWindow(QMainWindow):
    def __init__(self, store: TaskStore | None = None):
        super().__init__()
        self.setWindowTitle("CardMaker · 一键把 PDF 变成 Anki 卡片")
        self.resize(1200, 820)
        self.setMinimumSize(1000, 700)
        self.store = store or TaskStore()
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
        history_button = QPushButton("📜  历史")
        history_button.clicked.connect(self.open_history)
        settings_button = QPushButton("⚙  设置")
        settings_button.clicked.connect(lambda: self.open_settings())
        for button in (history_button, settings_button):
            top.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)
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
        self.import_page.resume_requested.connect(self.resume)
        self.options_page.back_requested.connect(lambda: self.go(0))
        self.options_page.start_requested.connect(self.start_generation)
        self.progress_page.cancel_requested.connect(self.cancel_generation)
        self.review_page.restart_requested.connect(self.restart)
        self.refresh_resume_banner()

    # ------------------------------------------------------------------ 导航
    def go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.steps.set_current(index)

    def to_options(self) -> None:
        self.options_page.set_document(self.import_page.path, self.import_page.info)
        self.go(1)

    def restart(self) -> None:
        self.import_page.clear()
        self.refresh_resume_banner()
        self.go(0)

    def refresh_resume_banner(self) -> None:
        unfinished = self.store.unfinished_tasks()
        self.import_page.show_unfinished(unfinished[0] if unfinished else None)

    def open_settings(self, message: str = "") -> bool:
        return SettingsDialog(self, message).exec() == SettingsDialog.DialogCode.Accepted

    def open_history(self) -> None:
        if self.is_generating():
            QMessageBox.information(self, "历史记录", "正在生成卡片，完成后再看历史记录吧。")
            return
        dialog = HistoryDialog(self, self.store)
        dialog.open_requested.connect(self.open_task)
        dialog.resume_requested.connect(self.resume)
        dialog.exec()

    def open_task(self, task_id: int) -> None:
        """从历史记录打开以前的卡片（包括当时的修改和导入 Anki 的结果）。"""
        result, template = load_result(self.store, task_id)
        self.template = template
        self.review_page.set_result(result, template, self.store)
        self.go(3)

    # ------------------------------------------------------------------ 生成
    def is_generating(self) -> bool:
        return self.worker is not None and self.worker.is_running()

    def ensure_api_key(self):
        settings = load_settings()
        if not settings.llm.api_key:
            if not self.open_settings("第一次使用，请先粘贴大模型的 API Key。"):
                return None
            settings = load_settings()
        return settings if settings.llm.api_key else None

    def start_generation(self) -> None:
        settings = self.ensure_api_key()
        if settings is None:
            return
        template, params, options = self.options_page.collect()
        self.template = template
        self._run(GenerateWorker(self.import_page.path, template, params, options, settings, store=self.store))

    def resume(self, task_id: int) -> None:
        """断点续传：只处理上次没完成或失败的页。"""
        if self.is_generating():
            return
        settings = self.ensure_api_key()
        if settings is None:
            return
        self._run(GenerateWorker.for_resume(task_id, settings, store=self.store))

    def _run(self, worker: GenerateWorker) -> None:
        self.worker = worker
        worker.progress.connect(self.progress_page.on_progress)
        worker.finished.connect(self.on_finished)
        worker.failed.connect(self.on_failed)
        self.progress_page.start()
        self.go(2)
        worker.start()

    def cancel_generation(self) -> None:
        if self.worker:
            self.worker.cancel()

    def on_finished(self, result: RunResult) -> None:
        self.progress_page.stop()
        self.worker = None
        if not result.cards:
            failed = [c.error for c in result.chunks if c.status == "failed"]
            reason = ("已取消。任务已保存，可以在第一步点「继续生成」接着做。" if result.cancelled else
                      f"所有页面都失败了：{failed[0]}" if failed else
                      "没有找到合适的内容。可以换个模板，或者把水平调低一点再试。")
            QMessageBox.information(self, "没有生成卡片", reason)
            self.refresh_resume_banner()
            self.go(0)
            return
        if self.template is None or self.template.id != result.template_id:
            self.template = load_template(result.template_id)
        self.review_page.set_result(result, self.template, self.store)
        self.refresh_resume_banner()
        self.go(3)

    def on_failed(self, message: str) -> None:
        self.progress_page.stop()
        self.worker = None
        QMessageBox.warning(self, "生成失败", f"{message}\n\n已经完成的页都保存好了，解决问题后可以在第一步点「继续生成」。")
        self.refresh_resume_banner()
        self.go(0)
        if "Key" in message:
            self.open_settings(message)

    def closeEvent(self, event) -> None:
        if self.is_generating():
            answer = QMessageBox.question(self, "正在生成", "卡片还在生成中，确定要退出吗？\n已经完成的页都保存好了，下次打开可以继续。")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
        self.review_page.save()
        event.accept()
