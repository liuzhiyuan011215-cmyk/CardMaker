import urllib.error
import urllib.request

import pymupdf
import pytest

from cardmaker.llm.client import Usage


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """所有测试都用临时目录当配置目录，绝不读写真实配置（里面有 API Key）。"""
    home = tmp_path / "home"
    monkeypatch.setenv("CARDMAKER_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """单元测试一律不联网（词典、大模型、语音都用假的）；需要的测试自己再换成假的 urlopen。"""
    def blocked(*args, **kwargs):
        raise urllib.error.URLError("单元测试不联网")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)


class FakeLLM:
    """按顺序返回预设内容的假模型，用来测试重试、校验等逻辑，不花 token。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages):
        self.calls.append(messages)
        reply = self.responses.pop(0) if self.responses else '{"items": []}'
        if isinstance(reply, Exception):
            raise reply
        return reply, Usage(prompt_tokens=100, completion_tokens=50, calls=1)


@pytest.fixture
def make_pdf(tmp_path):
    """生成测试 PDF。pages 是每页的文字；None 表示空白页。"""

    def _make(pages, name="test.pdf", toc=None, first_label=None):
        doc = pymupdf.open()
        for text in pages:
            page = doc.new_page(width=595, height=842)
            if text:
                page.insert_font(fontname="cjk", fontbuffer=pymupdf.Font("cjk").buffer)
                page.insert_textbox(pymupdf.Rect(50, 50, 545, 800), text, fontname="cjk", fontsize=11)
        if toc:
            doc.set_toc(toc)
        if first_label:
            doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": first_label}])
        path = tmp_path / name
        doc.save(path)
        return path

    return _make
