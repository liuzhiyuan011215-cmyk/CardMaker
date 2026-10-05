import io
import json
import urllib.error
import urllib.request

import pytest

from cardmaker.config import LLMSettings
from cardmaker.llm.client import FatalLLMError, OllamaClient, Usage, make_llm, parse_json_object


@pytest.mark.parametrize("text", [
    '{"items": []}',
    '```json\n{"items": []}\n```',
    '好的，结果如下：\n{"items": []}\n希望有帮助',
    '  \n```\n{"items": []}\n```  ',
])
def test_parse_json_object_variants(text):
    assert parse_json_object(text) == {"items": []}


@pytest.mark.parametrize("text", ["", "没有 JSON", "[1, 2, 3]", '{"items": [}'])
def test_parse_json_object_errors(text):
    with pytest.raises(ValueError):
        parse_json_object(text)


def test_usage_add():
    total = Usage()
    total.add(Usage(prompt_tokens=10, completion_tokens=5, cached_tokens=2, calls=1))
    total.add(Usage(prompt_tokens=1, completion_tokens=1, calls=1))
    assert (total.prompt_tokens, total.completion_tokens, total.cached_tokens, total.calls) == (11, 6, 2, 2)
    assert total.total_tokens == 17


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_local_model_needs_no_key_and_gets_a_long_context(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append((request.full_url, json.loads(request.data)))
        reply = {"message": {"content": '{"ok": true}'}, "prompt_eval_count": 30, "eval_count": 5}
        return FakeResponse(json.dumps(reply).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    settings = LLMSettings()
    settings.apply_preset("ollama")
    assert settings.ready and not settings.api_key  # 本地模型不要 Key
    text, usage = make_llm(settings).chat([{"role": "user", "content": "hi"}])
    assert text == '{"ok": true}' and (usage.prompt_tokens, usage.completion_tokens, usage.calls) == (30, 5, 1)
    url, body = sent[0]
    assert url == "http://localhost:11434/api/chat"
    assert body["options"]["num_ctx"] == 8192 and body["format"] == "json" and body["think"] is False
    assert body["keep_alive"] == "30s"  # 用完很快退出内存，8 GB 的 Mac 才不会卡


def test_local_model_errors_say_what_to_do(monkeypatch):
    def missing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", missing)
    with pytest.raises(FatalLLMError, match="请先在 Ollama 里下载"):
        OllamaClient("http://localhost:11434", "qwen3.5:2b").chat([])

    def refused(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    with pytest.raises(FatalLLMError, match="请先打开 Ollama"):
        OllamaClient("http://localhost:11434/v1", "qwen3.5:2b").chat([])
