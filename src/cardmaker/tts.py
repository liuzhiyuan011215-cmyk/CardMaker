"""发音（TTS）：给单词和例句生成音频。

- 三种方式：阿里云百炼的语音合成（默认 qwen-audio-3.1-tts-flash，音质好，注册有免费额度）；
  本地 Qwen3-TTS（mlx-audio，免费、不联网，第一次用自动下载模型）；
  系统自带语音（macOS 的 say 命令，免费、不联网、什么都不用装）；
- 缓存：按「模型 + 音色 + 格式 + 文字」的哈希存文件，同样的内容只合成一次，不重复收费；
- 选音色：按文字的语言自动选（韩语、中文用多语种音色，英语用英语音色），可在设置里改；
- 容错：某条合成失败只是这张卡没有声音，不影响导入。
"""
from __future__ import annotations

import base64
import hashlib
import html
import importlib.util
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import wave
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Settings, app_home

log = logging.getLogger(__name__)

ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"


class TTSError(Exception):
    pass


class TTSRateLimited(TTSError):
    """请求太频繁（HTTP 429），等一会儿再试就好。"""


def detect_language(text: str) -> str:
    """按文字数量判断语言：ko / zh / ja / en。"""
    counts = {"ko": 0, "zh": 0, "ja": 0, "en": 0}
    for ch in text:
        name = unicodedata.name(ch, "")
        if name.startswith("HANGUL"):
            counts["ko"] += 1
        elif name.startswith("CJK UNIFIED"):
            counts["zh"] += 1
        elif name.startswith(("HIRAGANA", "KATAKANA")):
            counts["ja"] += 1
        elif ch.isascii() and ch.isalpha():
            counts["en"] += 1
    return max(counts, key=counts.get) if any(counts.values()) else "en"


def plain_text(value: str) -> str:
    """卡片字段 → 要朗读的纯文字：去掉 HTML 标签和已有的 [sound:…]。"""
    value = re.sub(r"\[sound:[^\]]*\]", " ", value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        if exc.code in (401, 403):
            raise TTSError("语音合成的 API Key 不正确或没有权限") from exc
        if exc.code == 429:
            raise TTSRateLimited("语音合成请求太频繁") from exc
        raise TTSError(f"语音合成请求失败（HTTP {exc.code}）：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TTSError(f"连不上语音合成服务：{exc}") from exc


def _get_bytes(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


class CachedTTS:
    """按「模型 + 音色 + 格式 + 文字」缓存音频文件；子类只管怎么合成（_synthesize）。"""

    def __init__(self, model: str, voices: dict[str, str], fmt: str, cache_dir: Path | None = None):
        self.model, self.voices, self.fmt = model, voices, fmt
        self.cache_dir = cache_dir or app_home() / "audio_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def voice_for(self, text: str) -> str:
        return self.voices.get(detect_language(text)) or self.voices.get("default") or next(iter(self.voices.values()))

    def cache_path(self, text: str, voice: str) -> Path:
        key = hashlib.sha1(f"{self.model}|{voice}|{self.fmt}|{text}".encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"cardmaker_{key}.{self.fmt}"

    def is_cached(self, text: str) -> bool:
        text = plain_text(text)
        path = self.cache_path(text, self.voice_for(text))
        return path.exists() and path.stat().st_size > 0

    def audio_for(self, text: str) -> Path:
        """返回这段文字的音频文件；缓存里有就直接用。"""
        text = plain_text(text)
        if not text:
            raise TTSError("没有可以朗读的文字")
        voice = self.voice_for(text)
        path = self.cache_path(text, voice)
        if path.exists() and path.stat().st_size > 0:
            return path
        data = self._synthesize(text, voice)
        partial = path.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(path)  # 写完再改名，中途出错不会留下坏文件
        return path

    def _synthesize(self, text: str, voice: str) -> bytes:
        raise NotImplementedError


class DashScopeTTS(CachedTTS):
    def __init__(self, api_key: str, model: str, voices: dict[str, str], fmt: str = "mp3",
                 cache_dir: Path | None = None, post=_post_json, get=_get_bytes, timeout: float = 30):
        if not api_key:
            raise TTSError("没有设置 API Key，无法生成发音")
        super().__init__(model, voices, fmt, cache_dir)
        self.api_key = api_key
        self._post, self._get, self.timeout = post, get, timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "DashScopeTTS":
        tts = settings.tts
        return cls(tts.api_key or settings.llm.api_key, tts.model, tts.voices, tts.format)

    def _synthesize(self, text: str, voice: str) -> bytes:
        body = {"model": self.model, "input": {"text": text, "voice": voice, "format": self.fmt, "sample_rate": 24000}}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(4):  # 被限流时指数退避重试：1.5、3、6 秒
            try:
                result = self._post(ENDPOINT, body, headers, self.timeout)
                break
            except TTSRateLimited:
                if attempt == 3:
                    raise
                time.sleep(1.5 * 2 ** attempt)
        audio = (result.get("output") or {}).get("audio") or {}
        if audio.get("data"):
            return base64.b64decode(audio["data"])
        if audio.get("url"):
            return self._get(audio["url"], self.timeout)
        raise TTSError(f"语音合成没有返回音频：{str(result)[:200]}")


class SystemTTS(CachedTTS):
    """系统自带语音（macOS 的 say 命令）：免费、不联网、不用 Key，音质比百炼差一点。"""
    VOICES = {"ko": "Yuna", "zh": "Tingting", "ja": "Kyoko", "en": "Samantha"}  # macOS 自带的音色

    def __init__(self, cache_dir: Path | None = None, run=subprocess.run):
        if not shutil.which("say"):  # ponytail: 只支持 macOS；出 Windows 版时改用系统的 SAPI 语音
            raise TTSError("这台电脑没有系统语音（目前只支持 macOS），请在「设置」里改用百炼语音")
        super().__init__("say", self.VOICES, "m4a", cache_dir)
        self._run = run

    def _synthesize(self, text: str, voice: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audio.m4a"
            done = self._run(["say", "-v", voice, "--data-format=aac", "-o", str(out), "--", text],
                             capture_output=True, text=True, timeout=60)
            if done.returncode or not out.exists():
                raise TTSError(f"系统语音合成失败：{(done.stderr or '').strip()[:200]}")
            return out.read_bytes()


class QwenLocalTTS(CachedTTS):
    """本地 Qwen3-TTS（mlx-audio，Apple 芯片的 Mac）：免费、不联网、不用 Key；第一次用会自动下载模型（约 1.7 GB）。"""
    MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-4bit"  # 最小的版本，8 GB 内存的 Mac 也能跑
    VOICES = {"ko": "Sohee", "zh": "Vivian", "ja": "Ono_Anna", "en": "Aiden"}  # 母语音色；Ryan 在 4bit 版上读英语含糊、停不下来
    LANGUAGES = {"ko": "korean", "zh": "chinese", "ja": "japanese", "en": "english"}

    def __init__(self, cache_dir: Path | None = None, load=None):
        if load is None:
            if importlib.util.find_spec("mlx_audio") is None:
                raise TTSError("本地语音还没安装（需要 Apple 芯片的 Mac 和 mlx-audio），请在「设置」里改用别的发音方式")
            from mlx_audio.tts.utils import load
        super().__init__(self.MODEL, self.VOICES, "wav", cache_dir)
        self._load, self._model, self._lock = load, None, threading.Lock()

    def _synthesize(self, text: str, voice: str) -> bytes:
        import numpy as np

        language = self.LANGUAGES[detect_language(text)]
        if text[-1] not in ".!?。！？…":  # 单个词不带句号时模型常常停不下来（实测 bank 读成 6 秒）
            text += "."
        max_tokens = int(12.5 * (1.5 + 0.3 * len(text)))  # 每秒 12.5 个音频 token：按字数限制最长时长，防止跑飞
        with self._lock:  # 模型只加载一次；一次只合成一条
            if self._model is None:
                self._model = self._load(self.model)  # 第一次会从 Hugging Face 下载
            results = list(self._model.generate(text=text, voice=voice, lang_code=language, max_tokens=max_tokens))
        if not results:
            raise TTSError("本地语音没有生成音频")
        samples = np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:  # 16 位单声道 wav，Anki 电脑版和手机版都能播
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(results[0].sample_rate)
            out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        return buffer.getvalue()


def make_tts(settings: Settings) -> CachedTTS:
    """按设置选发音方式；选了百炼但没有 Key（例如只用本地模型）时，用系统自带语音。"""
    if settings.tts.engine == "qwen_local":
        return QwenLocalTTS()
    if settings.tts.engine == "system" or not (settings.tts.api_key or settings.llm.api_key):
        return SystemTTS()
    return DashScopeTTS.from_settings(settings)


def add_audio(cards: list, tts: CachedTTS, progress: Callable[[int, int], None] | None = None,
              workers: int = 2, stats: Counter | None = None) -> tuple[list[Path], list[str]]:
    """给单词卡配发音：Audio 读单词，ExampleAudio 读例句。

    返回（用到的音频文件，失败说明）。音频文件要和卡片一起导入 Anki（媒体文件）。
    stats：给了就记下新合成几条（new）、共多少字（new_chars），几条直接用了缓存（cached），统计费用用。
    """
    jobs = {plain_text(card.fields.get(field, "")) for card in cards for field in ("Front", "Example")}
    jobs.discard("")
    texts = sorted(jobs)
    cached = {text for text in texts if tts.is_cached(text)} if stats is not None else set()
    audio: dict[str, Path] = {}
    errors: list[str] = []

    def synthesize(text: str):
        try:
            return text, tts.audio_for(text), None
        except Exception as exc:  # 单条失败不影响其他
            return text, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (text, path, error) in enumerate(pool.map(synthesize, texts), 1):
            if path:
                audio[text] = path
            else:
                errors.append(f"{text[:20]}：{error}")
                log.warning("发音生成失败：%s %s", text[:20], error)
            if progress:
                progress(done, len(texts))
    if stats is not None:
        new = [text for text in audio if text not in cached]
        stats.update(new=len(new), new_chars=sum(map(len, new)), cached=len(cached))

    for card in cards:
        word = audio.get(plain_text(card.fields.get("Front", "")))
        example = audio.get(plain_text(card.fields.get("Example", "")))
        card.fields["Audio"] = f"[sound:{word.name}]" if word else ""
        card.fields["ExampleAudio"] = f"[sound:{example.name}]" if example else ""
    return sorted(set(audio.values())), errors
