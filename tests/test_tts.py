"""发音：用假的 HTTP 测试，不花钱、不联网。"""
import subprocess
import sys
import wave
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from cardmaker.config import Settings
from cardmaker.pipeline.result import Card
from cardmaker import tts as tts_module
from cardmaker.tts import (DashScopeTTS, QwenLocalTTS, SystemTTS, TTSError, TTSRateLimited, add_audio,
                           detect_language, make_tts, plain_text)

VOICES = {"ko": "ko_voice", "zh": "zh_voice", "en": "en_voice"}


class FakeHTTP:
    def __init__(self, fail_on=()):
        self.posts: list[dict] = []
        self.fail_on = set(fail_on)

    def post(self, url, body, headers, timeout):
        self.posts.append(body)
        if body["input"]["text"] in self.fail_on:
            raise TTSError("网络断了")
        return {"output": {"audio": {"url": f"https://example.com/{len(self.posts)}.mp3"}}}

    def get(self, url, timeout):
        return b"ID3fake-mp3-" + url.encode()


@pytest.fixture
def http():
    return FakeHTTP()


@pytest.fixture
def tts(tmp_path, http):
    return DashScopeTTS("sk-test", "qwen-audio-3.1-tts-flash", VOICES, cache_dir=tmp_path / "audio",
                        post=http.post, get=http.get)


def test_language_and_plain_text():
    assert [detect_language(t) for t in ("확산되다", "扩散", "alleviate")] == ["ko", "zh", "en"]
    assert plain_text("그는 <b>매일</b> 간다. [sound:a.mp3] &amp;") == "그는 매일 간다. &"


def test_audio_is_cached_and_voice_follows_language(tts, http):
    first = tts.audio_for("확산되다")
    again = tts.audio_for("확산되다")
    assert first == again and first.read_bytes().startswith(b"ID3") and len(http.posts) == 1  # 第二次走缓存
    tts.audio_for("alleviate")
    assert [p["input"]["voice"] for p in http.posts] == ["ko_voice", "en_voice"]
    assert http.posts[0]["model"] == "qwen-audio-3.1-tts-flash"


def test_add_audio_fills_fields_and_tolerates_failures(tmp_path):
    http = FakeHTTP(fail_on={"빈집이 방치되어 있다."})
    tts = DashScopeTTS("sk-test", "m", VOICES, cache_dir=tmp_path / "a", post=http.post, get=http.get)
    cards = [Card({"Front": "확산되다", "Example": "감기가 <b>확산되었다</b>."}, {}, "1"),
             Card({"Front": "방치되다", "Example": "빈집이 방치되어 있다."}, {}, "1"),
             Card({"Front": "확산되다", "Example": ""}, {}, "2")]
    media, errors = add_audio(cards, tts, workers=2)
    assert cards[0].fields["Audio"].startswith("[sound:cardmaker_") and cards[0].fields["ExampleAudio"]
    assert cards[1].fields["Audio"] and cards[1].fields["ExampleAudio"] == ""  # 例句失败：只是没声音
    assert cards[2].fields["Audio"] == cards[0].fields["Audio"]  # 同一个词只合成一次
    assert len(errors) == 1 and len(media) == 3 and len(http.posts) == 4


def test_add_audio_counts_new_and_cached_clips(tts, http):
    """统计发音用量：新合成几条、多少字（按量计费），再导入一次全部走缓存，不再花钱。"""
    cards = [Card({"Front": "확산되다", "Example": "감기가 확산되었다."}, {}, "1")]
    first, again = Counter(), Counter()
    add_audio(cards, tts, stats=first)
    add_audio(cards, tts, stats=again)
    assert (first["new"], first["new_chars"], first["cached"]) == (2, len("확산되다감기가 확산되었다."), 0)
    assert (again["new"], again["cached"]) == (0, 2) and len(http.posts) == 2


def test_missing_key():
    with pytest.raises(TTSError):
        DashScopeTTS("", "m", VOICES)


def test_rate_limit_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_module.time, "sleep", lambda s: None)
    calls = []

    def post(url, body, headers, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise TTSRateLimited("请求太频繁")  # 前两次被限流
        return {"output": {"audio": {"url": "https://example.com/a.mp3"}}}

    tts = DashScopeTTS("sk-test", "m", VOICES, cache_dir=tmp_path, post=post, get=lambda url, timeout: b"ID3")
    assert tts.audio_for("합의").read_bytes() == b"ID3" and len(calls) == 3


def test_system_voice_is_free_and_cached(tmp_path):
    calls = []

    if sys.platform == "win32":
        def fake_win(args, **kwargs):
            calls.append(args)
            for part in args[-1].split("'"):
                if part.endswith(".wav"):
                    Path(part).write_bytes(b"wav-audio")
            return subprocess.CompletedProcess(args, 0, "", "")

        tts = SystemTTS(cache_dir=tmp_path, run=fake_win)
        path = tts.audio_for("배를 깎아 먹었다.")
        assert path.suffix == ".wav" and path.read_bytes() == b"wav-audio"
        assert calls[0][0] == "powershell" and "ko-KR" in calls[0][-1]
    else:
        def fake_say(args, **kwargs):
            calls.append(args)
            Path(args[args.index("-o") + 1]).write_bytes(b"m4a-audio")
            return subprocess.CompletedProcess(args, 0, "", "")

        tts = SystemTTS(cache_dir=tmp_path, run=fake_say)
        path = tts.audio_for("배를 깎아 먹었다.")
        assert path.suffix == ".m4a" and path.read_bytes() == b"m4a-audio"
        assert calls[0][:3] == ["say", "-v", "Yuna"] and calls[0][-1] == "배를 깎아 먹었다."  # 韩语用韩语音色

    tts.audio_for("배를 깎아 먹었다.")
    assert len(calls) == 1  # 第二次走缓存


def test_make_tts_uses_system_voice_without_key():
    settings = Settings()  # 没有百炼 Key（例如只用本地模型）
    assert isinstance(make_tts(settings), SystemTTS)
    settings.llm.api_key = "sk-test"
    assert isinstance(make_tts(settings), DashScopeTTS)
    settings.tts.engine = "system"
    assert isinstance(make_tts(settings), SystemTTS)


def test_local_qwen_tts_loads_the_model_once_and_writes_wav(tmp_path):
    loads, calls = [], []

    class FakeModel:
        def generate(self, text, voice, lang_code, max_tokens):
            calls.append((text, voice, lang_code))
            yield SimpleNamespace(audio=[0.0, 0.5, -0.5], sample_rate=24000)

    def load(repo):
        loads.append(repo)
        return FakeModel()

    tts = QwenLocalTTS(cache_dir=tmp_path, load=load)
    path = tts.audio_for("배를 깎아 먹었다.")
    tts.audio_for("alleviate")
    assert loads == [QwenLocalTTS.MODEL]
    assert calls == [("배를 깎아 먹었다.", "Sohee", "korean"), ("alleviate.", "Aiden", "english")]  # 单词补上句号
    with wave.open(str(path)) as audio:
        assert (audio.getframerate(), audio.getnframes()) == (24000, 3)


def test_local_qwen_tts_requires_mac():
    if sys.platform == "win32":
        with pytest.raises(TTSError, match="Apple"):
            QwenLocalTTS()
