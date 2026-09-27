"""发音（TTS）：用阿里云百炼的语音合成（默认 qwen-audio-3.1-tts-flash）给单词和例句生成音频。

- 缓存：按「模型 + 音色 + 格式 + 文字」的哈希存文件，同样的内容只合成一次，不重复收费；
- 选音色：按文字的语言自动选（韩语、中文用多语种音色，英语用英语音色），可在设置里改；
- 容错：某条合成失败只是这张卡没有声音，不影响导入。
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.request
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


class DashScopeTTS:
    def __init__(self, api_key: str, model: str, voices: dict[str, str], fmt: str = "mp3",
                 cache_dir: Path | None = None, post=_post_json, get=_get_bytes, timeout: float = 30):
        if not api_key:
            raise TTSError("没有设置 API Key，无法生成发音")
        self.api_key, self.model, self.voices, self.fmt = api_key, model, voices, fmt
        self.cache_dir = cache_dir or app_home() / "audio_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._post, self._get, self.timeout = post, get, timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "DashScopeTTS":
        tts = settings.tts
        return cls(tts.api_key or settings.llm.api_key, tts.model, tts.voices, tts.format)

    def voice_for(self, text: str) -> str:
        return self.voices.get(detect_language(text)) or self.voices.get("default") or next(iter(self.voices.values()))

    def cache_path(self, text: str, voice: str) -> Path:
        key = hashlib.sha1(f"{self.model}|{voice}|{self.fmt}|{text}".encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"cardmaker_{key}.{self.fmt}"

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


def add_audio(cards: list, tts: DashScopeTTS, progress: Callable[[int, int], None] | None = None,
              workers: int = 2) -> tuple[list[Path], list[str]]:
    """给单词卡配发音：Audio 读单词，ExampleAudio 读例句。

    返回（用到的音频文件，失败说明）。音频文件要和卡片一起导入 Anki（媒体文件）。
    """
    jobs = {plain_text(card.fields.get(field, "")) for card in cards for field in ("Front", "Example")}
    jobs.discard("")
    texts = sorted(jobs)
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

    for card in cards:
        word = audio.get(plain_text(card.fields.get("Front", "")))
        example = audio.get(plain_text(card.fields.get("Example", "")))
        card.fields["Audio"] = f"[sound:{word.name}]" if word else ""
        card.fields["ExampleAudio"] = f"[sound:{example.name}]" if example else ""
    return sorted(set(audio.values())), errors
