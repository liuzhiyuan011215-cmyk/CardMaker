"""主窗口：四步向导（选文件 → 选用途 → 生成 → 检查并导入），外加历史记录和断点续传。"""
from __future__ import annotations

from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..config import load_settings
from ..i18n import tr
from ..pipeline.result import RunResult
from ..pipeline.runner import load_result
from ..storage import TaskStore
from ..templates.loader import load_template
from .history_dialog import HistoryDialog
from .progress_page import ProgressPage
from .purpose_page import PurposePage, shown_template_ids
from .review_page import ReviewPage
from .settings_dialog import SettingsDialog
from .source_page import SourcePage
from .template_editor import TemplateEditorDialog
from .widgets import Backdrop, Picture, StepIndicator, button, label
from .worker import GenerateWorker

STEPS = ["选择用途", "选择资料", "生成卡片", "检查并导入"]


class MainWindow(QMainWindow):
    def __init__(self, store: TaskStore | None = None):
        super().__init__()
        self.setWindowTitle(tr("CardMaker · 一键把 PDF 变成 Anki 卡片"))
        self.resize(1200, 820)
        self.setMinimumSize(1000, 700)
        self.store = store or TaskStore()
        self.worker: GenerateWorker | None = None
        self.template = None

        # 三层：玻璃底（Backdrop）→ 浮在上面的白色面板（Sheet，放各步页面）→ 面板里的卡片
        central = Backdrop()
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 0, 24, 24)
        root.setSpacing(0)

        # 顶部：左边名字，中间步骤条，右边历史和设置（三列，左右两列一样宽，步骤条正好居中）
        header = QWidget()
        header.setObjectName("Header")
        head = QGridLayout(header)
        head.setContentsMargins(8, 18, 8, 18)
        head.setColumnStretch(0, 1)
        head.setColumnStretch(2, 1)
        top = QHBoxLayout()
        top.addWidget(Picture("icon.png", 42))  # 和 Dock 里的 App 图标是同一张
        top.addSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("CardMaker", "AppTitle"))
        self.subtitle = label("把单词书、阅读材料一键变成 Anki 卡片", "AppSubtitle")
        titles.addWidget(self.subtitle)
        top.addLayout(titles)
        top.addStretch()
        head.addLayout(top, 0, 0)
        self.steps = StepIndicator(STEPS)
        head.addWidget(self.steps, 0, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        history_button = button("历史", "ghost", icon="history")
        history_button.clicked.connect(self.open_history)
        settings_button = button("设置", "ghost", icon="settings")
        settings_button.clicked.connect(lambda: self.open_settings())
        for widget in (history_button, settings_button):
            actions.addWidget(widget)
        head.addLayout(actions, 0, 2)
        root.addWidget(header)

        self.stack = QStackedWidget()
        self.purpose_page = PurposePage()
        self.source_page = SourcePage()
        self.progress_page = ProgressPage()
        self.review_page = ReviewPage()
        for page in (self.purpose_page, self.source_page, self.progress_page, self.review_page):
            self.stack.addWidget(page)
        sheet = QFrame()
        sheet.setObjectName("Sheet")
        sheet_layout = QVBoxLayout(sheet)
        sheet_layout.setContentsMargins(0, 0, 0, 0)
        sheet_layout.addWidget(self.stack)
        central.sheet = sheet
        root.addWidget(sheet, 1)
        self.setCentralWidget(central)

        self.purpose_page.next_requested.connect(self.to_source)
        self.purpose_page.resume_requested.connect(self.resume)
        self.purpose_page.edit_template_requested.connect(self.edit_templates)
        self.source_page.back_requested.connect(lambda: self.go(0))
        self.source_page.start_requested.connect(self.start_generation)
        self.progress_page.cancel_requested.connect(self.cancel_generation)
        self.review_page.restart_requested.connect(self.restart)
        self.refresh_resume_banner()

    # ------------------------------------------------------------------ 导航
    def go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.steps.set_current(index)

    def to_source(self) -> None:
        if self.purpose_page.group:
            self.source_page.set_group(self.purpose_page.group)
            self.go(1)

    def restart(self) -> None:
        self.source_page.clear_file()
        self.refresh_resume_banner()
        self.go(0)

    def refresh_resume_banner(self) -> None:
        shown = shown_template_ids()
        unfinished = [t for t in self.store.unfinished_tasks() if t.template_id in shown]
        self.purpose_page.show_unfinished(unfinished[0] if unfinished else None)

    def edit_templates(self) -> None:
        group = self.purpose_page.group
        template = group.variants[0] if group else None
        if TemplateEditorDialog(self, template).exec():
            self.purpose_page.reload()

    def open_settings(self, message: str = "") -> bool:
        return SettingsDialog(self, message).exec() == SettingsDialog.DialogCode.Accepted

    def open_history(self) -> None:
        if self.is_generating():
            QMessageBox.information(self, tr("历史记录"), tr("正在生成卡片，完成后再看历史记录吧。"))
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
        if not settings.llm.ready:
            if not self.open_settings("第一次使用，请先粘贴大模型的 API Key（或者选「本地模型」）。"):
                return None
            settings = load_settings()
        return settings if settings.llm.ready else None

    def start_generation(self) -> None:
        settings = self.ensure_api_key()
        if settings is None:
            return
        template, params, options = self.source_page.collect()
        self.template = template
        self._run(GenerateWorker(self.source_page.path, template, params, options, settings, store=self.store))

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
                      "没有找到合适的内容。可以换个用途，或者调整选项（例如把水平调低一点）再试。")
            QMessageBox.information(self, tr("没有生成卡片"), tr(reason))
            self.refresh_resume_banner()
            self.go(0 if result.cancelled else 1)
            return
        if self.template is None or self.template.id != result.template_id:
            self.template = load_template(result.template_id)
        self.review_page.set_result(result, self.template, self.store)
        self.refresh_resume_banner()
        self.go(3)

    def on_failed(self, message: str) -> None:
        self.progress_page.stop()
        self.worker = None
        QMessageBox.warning(self, tr("生成失败"), tr(f"{message}\n\n已经完成的页都保存好了，解决问题后可以在第一步点「继续生成」。"))
        self.refresh_resume_banner()
        self.go(0)
        if "Key" in message:
            self.open_settings(message)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.subtitle.setVisible(self.width() >= 1120)  # 窗口窄时藏起副标题，给步骤条让位

    def closeEvent(self, event) -> None:
        if self.is_generating():
            answer = QMessageBox.question(self, tr("正在生成"), tr("卡片还在生成中，确定要退出吗？\n已经完成的页都保存好了，下次打开可以继续。"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
        self.review_page.save()
        event.accept()
