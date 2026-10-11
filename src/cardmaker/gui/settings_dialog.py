"""设置窗口：选大模型平台、粘贴 API Key、测试连接。"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from .. import i18n
from ..config import LLM_PRESETS, LLMSettings, clean_pasted_key, load_settings, mask_key, save_settings
from ..i18n import tr
from ..llm.client import check_connection
from .widgets import StatusLine, label
from .worker import FunctionTask

PLATFORMS = [("阿里云百炼（通义千问）", "dashscope"), ("DeepSeek", "deepseek"),
             ("本地模型（Ollama，免费、不用注册）", "ollama"), ("其他（自己填地址和模型）", "custom")]
TTS_ENGINES = [("百炼语音（音质好，用百炼的 Key）", "dashscope"),
               ("本地 Qwen3-TTS（免费、不联网，第一次用自动下载约 1.7 GB）", "qwen_local"),
               ("系统自带语音（免费、不联网、什么都不用装）", "system")]
# qwen-audio-3.1-tts-flash 的音色：能读韩语的只有这 4 个多语种音色（也能读中文、英语）
MULTI_VOICES = [("安欢（女声）", "longanhuan_v3.1"), ("安灵心（女声）", "longanlingxin_v3.1"),
                ("安风月（女声）", "longanfengyue_v3.1"), ("讯安川（男声）", "xunanchuan_v3.1")]
ENGLISH_VOICES = [("Emily（英式女声）", "Emily_v3.1"), ("Brian（美式男声）", "Brian_v3.1")] + MULTI_VOICES
LANGUAGES = ["中文", "韩语", "英语"]


class SettingsDialog(QDialog):
    def __init__(self, parent=None, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("设置"))
        self.setMinimumWidth(640)
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
            self.platform.addItem(tr(text), key)
        index = self.platform.findData(self.settings.llm.provider)
        self.platform.setCurrentIndex(index if index >= 0 else len(PLATFORMS) - 1)
        form.addRow(tr("大模型平台"), self.platform)

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        current = self.settings.llm.api_key
        self.key.setPlaceholderText(tr(f"已设置（{mask_key(current)}）。要更换就在这里粘贴新的"
                                    if current else "在这里粘贴 API Key（通常以 sk- 开头）"))
        show = QCheckBox(tr("显示"))
        show.toggled.connect(lambda on: self.key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        form.addRow("API Key", key_row)
        self.key.setEnabled(self.settings.llm.provider != "ollama")  # 本地模型不要 Key

        self.model = QLineEdit(self.settings.llm.model)
        self.url = QLineEdit(self.settings.llm.base_url)
        form.addRow(tr("模型"), self.model)
        form.addRow(tr("接口地址"), self.url)
        price_row = QHBoxLayout()
        self.price_in, self.price_out = QDoubleSpinBox(), QDoubleSpinBox()
        for name, box, value in (("输入", self.price_in, self.settings.llm.price_input_per_m),
                                 ("输出", self.price_out, self.settings.llm.price_output_per_m)):
            box.setRange(0, 1000)
            box.setDecimals(3)
            box.setMinimumWidth(100)  # 放得下「0.000」，不被挤成半个数字
            box.setValue(value)
            box.setToolTip(tr("在大模型平台的价格说明里查。填 0 就只显示 token 数，不算费用；本地模型不花钱，不用填。"))
            price_row.addWidget(label(name, "Hint"))
            price_row.addWidget(box)
            price_row.addSpacing(16)
        price_row.addWidget(label("元 / 百万 token", "Hint"))
        price_row.addStretch()
        form.addRow(tr("单价"), price_row)
        self._enable_prices(self.settings.llm.provider)
        self.language = self._combo([(lang, lang) for lang in LANGUAGES], self.settings.meaning_language)
        form.addRow(tr("释义默认用"), self.language)
        self.ui_language = self._combo(i18n.LANGUAGES, self.settings.ui_language or i18n.LANGUAGE)
        self.ui_language.setToolTip(tr("重新打开程序后生效"))
        form.addRow(tr("界面语言"), self.ui_language)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 10)
        self.concurrency.setValue(self.settings.concurrency)
        self.concurrency.setSuffix(tr(" 页"))
        self.concurrency.setToolTip(tr("越大越快；如果经常提示「请求太频繁」，就调小一点"))
        form.addRow(tr("同时处理"), self.concurrency)

        self.tts_enabled = QCheckBox(tr("导入 Anki 时给单词和例句配上发音"))
        self.tts_enabled.setChecked(self.settings.tts.enabled)
        form.addRow(tr("发音"), self.tts_enabled)
        self.tts_engine = self._combo(TTS_ENGINES, self.settings.tts.engine)
        form.addRow(tr("发音方式"), self.tts_engine)
        self.voice_ko = self._combo(MULTI_VOICES, self.settings.tts.voices.get("ko"))
        self.voice_en = self._combo(ENGLISH_VOICES, self.settings.tts.voices.get("en"))
        form.addRow(tr("韩语 / 中文音色"), self.voice_ko)
        form.addRow(tr("英语音色"), self.voice_en)

        self.dict_enabled = QCheckBox(tr("用词典核对单词释义，防止 AI 写错意思"))
        self.dict_enabled.setToolTip(tr("用词典核对单词释义，防止 AI 写错意思（韩语查《韩国语基础词典》，英语、中文查维基词典）"))
        self.dict_enabled.setChecked(self.settings.dictionary.enabled)
        form.addRow(tr("词典核对"), self.dict_enabled)
        self.dict_key = QLineEdit()
        self.dict_key.setEchoMode(QLineEdit.EchoMode.Password)
        current_dict = self.settings.dictionary.krdict_key
        self.dict_key.setPlaceholderText(tr(f"已设置（{mask_key(current_dict)}）。要更换就在这里粘贴新的" if current_dict
                                         else "免费申请：krdict.korean.go.kr → 오픈 API（只需邮箱）；不填也能用维基词典"))
        form.addRow(tr("词典 Key"), self.dict_key)
        layout.addLayout(form)
        layout.addWidget(label("API Key 只保存在这台电脑上（~/Library/Application Support/CardMaker），"
                               "不会上传到除大模型平台以外的任何地方。", "Hint", wrap=True))

        test_row = QHBoxLayout()
        self.test_button = QPushButton(tr("测试连接"))
        self.test_button.clicked.connect(self._test)
        self.test_result = StatusLine()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        layout.addLayout(test_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("保存"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.platform.currentIndexChanged.connect(self._platform_changed)

    @staticmethod
    def _combo(choices, current) -> QComboBox:
        combo = QComboBox()
        for text, voice in choices:
            combo.addItem(tr(text), voice)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        return combo

    # ------------------------------------------------------------------
    def _platform_changed(self) -> None:
        key = self.platform.currentData()
        if key in LLM_PRESETS:
            self.model.setText(LLM_PRESETS[key]["model"])
            self.url.setText(LLM_PRESETS[key]["base_url"])
        self.key.setEnabled(key != "ollama")
        self._enable_prices(key)
        if key == "ollama":  # 本地模型一次只算一页，同时发多页只会排队超时
            self.concurrency.setValue(1)

    def _enable_prices(self, provider: str) -> None:
        for box in (self.price_in, self.price_out):  # 本地模型不花钱
            box.setEnabled(provider != "ollama")

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
        llm.price_input_per_m, llm.price_output_per_m = self.price_in.value(), self.price_out.value()
        typed, _ = clean_pasted_key(self.key.text())
        if typed:
            llm.api_key = typed
        return llm

    def _test(self) -> None:
        llm = self.form_llm_settings()
        if not llm.ready:
            self.test_result.setText(tr("请先粘贴 API Key。"))
            return
        self.test_button.setEnabled(False)
        self.test_result.setText(tr("正在连接…"))
        self._task = FunctionTask(lambda: check_connection(llm))
        # 信号连到本窗口的方法（而不是 lambda），Qt 才会把它们排到界面线程执行
        self._task.finished.connect(self._on_test_ok)
        self._task.failed.connect(self._on_test_failed)
        self._task.start()

    def _on_test_ok(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.set(message, "ok")

    def _on_test_failed(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.set(message, "error")

    def accept(self) -> None:
        self.settings.llm = self.form_llm_settings()
        self.settings.meaning_language = self.language.currentData()
        self.settings.ui_language = self.ui_language.currentData()
        self.settings.concurrency = self.concurrency.value()
        self.settings.tts.enabled = self.tts_enabled.isChecked()
        self.settings.tts.engine = self.tts_engine.currentData()
        voice = self.voice_ko.currentData()
        self.settings.tts.voices.update({"ko": voice, "zh": voice, "ja": voice, "en": self.voice_en.currentData()})
        self.settings.dictionary.enabled = self.dict_enabled.isChecked()
        typed_dict, _ = clean_pasted_key(self.dict_key.text())
        if typed_dict:
            self.settings.dictionary.krdict_key = typed_dict
        save_settings(self.settings)
        super().accept()
