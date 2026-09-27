"""发音：用假的 HTTP 测试，不花钱、不联网。"""
import pytest

from cardmaker.pipeline.result import Card
from cardmaker import tts as tts_module
from cardmaker.tts import DashScopeTTS, TTSError, TTSRateLimited, add_audio, detect_language, plain_text

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
