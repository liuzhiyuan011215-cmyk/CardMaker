"""API OCR：用假的大模型测试看图识字、按行切分，不联网、不花 token。"""
import numpy as np

from cardmaker.ingest.ocr.api import ApiOCR
from cardmaker.llm.client import Usage


class FakeVisionLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> tuple[str, Usage]:
        self.calls.append(messages)
        return self.reply, Usage(calls=1)


def test_api_ocr_sends_png_and_splits_lines():
    llm = FakeVisionLLM('{"text": "1. allocate\\n\\n2. negotiate"}')
    ocr = ApiOCR(llm)
    image = np.zeros((16, 16, 3), dtype=np.uint8)

    lines = ocr.recognize(image)

    assert [line.text for line in lines] == ["1. allocate", "2. negotiate"]
    parts = llm.calls[0][0]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
