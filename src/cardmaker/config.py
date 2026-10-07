"""应用配置：读写用户数据目录下的 config.yaml。

配置文件放在系统的用户数据目录里（macOS 为 ~/Library/Application Support/CardMaker/），
不在项目目录中，所以 API Key 不会被提交到 git。
测试时可以用环境变量 CARDMAKER_HOME 把它指向临时目录。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from platformdirs import user_data_dir
from pydantic import BaseModel, Field

APP_NAME = "CardMaker"

# 大模型预设：只需填 Key 即可使用；base_url / model 也可以在配置里手动改
LLM_PRESETS: dict[str, dict] = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-flash",
        "extra_body": {"enable_thinking": False},  # Qwen3 系列关闭思考模式，更快更省
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "extra_body": {},
    },
    # 本地模型：装好 Ollama（ollama.com）、下载模型就能用，不用注册、不要 Key、不花钱（见 llm/client.py 的 OllamaClient）
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "qwen3.5:2b",  # 8 GB 内存的 Mac 也带得动；4b 会卡死
        "extra_body": {},
    },
}


class LLMSettings(BaseModel):
    provider: str = "dashscope"
    base_url: str = LLM_PRESETS["dashscope"]["base_url"]
    model: str = LLM_PRESETS["dashscope"]["model"]
    api_key: str = ""
    json_mode: bool = True
    temperature: float = 0.3
    timeout: float = 120
    extra_body: dict = Field(default_factory=lambda: dict(LLM_PRESETS["dashscope"]["extra_body"]))
    # 每百万 token 单价（元），在「设置」里填；0 表示不算费用，只显示 token
    price_input_per_m: float = 0.0
    price_output_per_m: float = 0.0

    @property
    def ready(self) -> bool:
        """填了 Key，或者用的是不要 Key 的本地模型。"""
        return bool(self.api_key) or self.provider == "ollama"

    def billing(self) -> dict:
        """生成时随任务保存的模型名和单价（GenerateOptions 的同名字段），统计费用用。本地模型不花钱。"""
        return {"model": self.model, "local_model": self.provider == "ollama",
                "price_input_per_m": self.price_input_per_m, "price_output_per_m": self.price_output_per_m}

    def apply_preset(self, name: str) -> None:
        preset = LLM_PRESETS[name]
        self.provider = name
        self.base_url = preset["base_url"]
        self.model = preset["model"]
        self.extra_body = dict(preset["extra_body"])


class OcrSettings(BaseModel):
    langs: list[str] = Field(default_factory=lambda: ["zh", "ko"])  # 参与融合识别的语言模型
    dpi: int = 200


class TTSSettings(BaseModel):
    """发音（语音合成），默认用百炼的 qwen-audio-3.1-tts-flash；也可以用系统自带语音（免费）。"""
    enabled: bool = True
    engine: str = "dashscope"  # dashscope：百炼（音质好）；qwen_local：本地 Qwen3-TTS；system：系统自带语音
    model: str = "qwen-audio-3.1-tts-flash"
    api_key: str = ""  # 留空则用大模型的 Key（都是百炼时同一个 Key）
    # 按文字语言选音色：多语种音色能读韩语和中文；英语用英语音色
    voices: dict[str, str] = Field(default_factory=lambda: {
        "ko": "longanhuan_v3.1", "zh": "longanhuan_v3.1", "ja": "longanhuan_v3.1", "en": "Emily_v3.1"})
    format: str = "mp3"


class DictionarySettings(BaseModel):
    """用词典核对单词释义：韩语单词查《韩国语基础词典》（Key 免费申请：krdict.korean.go.kr → 오픈 API），
    英语、中文单词（以及没填 Key 时的韩语单词）查维基词典，不用 Key。"""
    enabled: bool = True
    krdict_key: str = ""


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    dictionary: DictionarySettings = Field(default_factory=DictionarySettings)
    meaning_language: str = "中文"
    concurrency: int = 5
    anki_url: str = "http://127.0.0.1:8765"
    default_deck: str = "CardMaker"
    ui_language: str = ""  # 界面语言 zh / ko；空 = 跟随系统


def app_home() -> Path:
    return Path(os.environ.get("CARDMAKER_HOME") or user_data_dir(APP_NAME, appauthor=False))


def config_path() -> Path:
    return app_home() / "config.yaml"


def log_dir() -> Path:
    return app_home() / "logs"


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    if not path.exists():
        return Settings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(settings.model_dump(), allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)  # 只有当前用户可读写
    return path


def mask_key(key: str) -> str:
    """显示用：只保留首尾几位，日志和界面上永远不出现完整 Key。"""
    if not key:
        return "（未设置）"
    return f"{key[:3]}****{key[-4:]}" if len(key) > 10 else "****"


def clean_pasted_key(raw: str) -> tuple[str, str]:
    """整理粘贴进来的 Key，返回 (key, 提示)。

    输入时屏幕不显示，用户容易以为没粘上而重复粘贴，所以同一个 Key 粘了多次时只保留一份；
    同时去掉空白和终端的「括号粘贴」控制符。
    """
    key = "".join(re.sub(r"\x1b\[20[01]~", "", raw).split())
    if key.startswith("sk-") and key.count("sk-") > 1:
        return "sk-" + key[3:].split("sk-", 1)[0], "检测到同一个 Key 被粘贴了多次，已只保留一份。"
    return key, ""
