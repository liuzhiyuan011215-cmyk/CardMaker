"""设置窗口：选大模型平台、粘贴 API Key、测试连接。"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLineEdit, QPushButton, QVBoxLayout)

from ..config import LLM_PRESETS, LLMSettings, clean_pasted_key, load_settings, mask_key, save_settings
from ..llm.client import check_connection
from .widgets import label
from .worker import FunctionTask

PLATFORMS = [("阿里云百炼（通义千问）", "dashscope"), ("DeepSeek", "deepseek"), ("其他（自己填地址和模型）", "custom")]
LANGUAGES = ["中文", "韩语", "英语"]


class SettingsDialog(QDialog):
    def __init__(self, parent=None, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(600)
        self.settings = load_settings()
        self._task: FunctionTask | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        if message:
            layout.addWidget(label(message, "Big", wrap=True))

        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.platform = QComboBox()
        for text, key in PLATFORMS:
            self.platform.addItem(text, key)
        index = self.platform.findData(self.settings.llm.provider)
        self.platform.setCurrentIndex(index if index >= 0 else len(PLATFORMS) - 1)
        form.addRow("大模型平台", self.platform)

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        current = self.settings.llm.api_key
        self.key.setPlaceholderText(f"已设置（{mask_key(current)}）。要更换就在这里粘贴新的"
                                    if current else "在这里粘贴 API Key（通常以 sk- 开头）")
        show = QCheckBox("显示")
        show.toggled.connect(lambda on: self.key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        form.addRow("API Key", key_row)

        self.model = QLineEdit(self.settings.llm.model)
        self.url = QLineEdit(self.settings.llm.base_url)
        form.addRow("模型", self.model)
        form.addRow("接口地址", self.url)
        self.language = QComboBox()
        self.language.addItems(LANGUAGES)
        self.language.setCurrentText(self.settings.meaning_language)
        form.addRow("释义默认用", self.language)
        layout.addLayout(form)
        layout.addWidget(label("API Key 只保存在这台电脑上（~/Library/Application Support/CardMaker），"
                               "不会上传到除大模型平台以外的任何地方。", "Hint", wrap=True))

        test_row = QHBoxLayout()
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._test)
        self.test_result = label("", "Hint", wrap=True)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        layout.addLayout(test_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.platform.currentIndexChanged.connect(self._platform_changed)

    # ------------------------------------------------------------------
    def _platform_changed(self) -> None:
        key = self.platform.currentData()
        if key in LLM_PRESETS:
            self.model.setText(LLM_PRESETS[key]["model"])
            self.url.setText(LLM_PRESETS[key]["base_url"])

    def form_llm_settings(self) -> LLMSettings:
        """按窗口里填的内容组装一份大模型设置（没粘贴新 Key 时沿用原来的）。"""
        llm = self.settings.llm.model_copy(deep=True)
        provider = self.platform.currentData()
        if provider in LLM_PRESETS:
            llm.apply_preset(provider)
        else:
            llm.provider, llm.extra_body = "custom", {}
        llm.model = self.model.text().strip()
        llm.base_url = self.url.text().strip()
        typed, _ = clean_pasted_key(self.key.text())
        if typed:
            llm.api_key = typed
        return llm

    def _test(self) -> None:
        llm = self.form_llm_settings()
        if not llm.api_key:
            self.test_result.setText("请先粘贴 API Key。")
            return
        self.test_button.setEnabled(False)
        self.test_result.setText("正在连接…")
        self._task = FunctionTask(lambda: check_connection(llm))
        # 信号连到本窗口的方法（而不是 lambda），Qt 才会把它们排到界面线程执行
        self._task.finished.connect(self._on_test_ok)
        self._task.failed.connect(self._on_test_failed)
        self._task.start()

    def _on_test_ok(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(f"✅ {message}")

    def _on_test_failed(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(f"❌ {message}")

    def accept(self) -> None:
        self.settings.llm = self.form_llm_settings()
        self.settings.meaning_language = self.language.currentText()
        save_settings(self.settings)
        super().accept()
