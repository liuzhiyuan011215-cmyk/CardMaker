"""用当前大模型直接看图识字（API OCR）：解决斜拍、双栏、便利贴混行问题。"""
from __future__ import annotations

import base64
import io

from PIL import Image

from ...llm.client import ChatModel, parse_json_object
from .base import OcrLine

PROMPT = (
    "原样抄出这张图里的所有文字，不要翻译，不要修改拼写，也不要解释。"
    "如果有多栏，按先左栏后右栏、从上到下排列。"
    '只输出一个 json 对象：{"text": "整页文字，按换行符分隔"}'
)


class ApiOCR:
    name = "api"

    def __init__(self, llm: ChatModel):
        self.llm = llm

    def recognize(self, image) -> list[OcrLine]:
        buf = io.BytesIO()
        Image.fromarray(image).save(buf, format="PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": url}},
        ]}]
        content, _ = self.llm.chat(messages)
        try:
            val = parse_json_object(content).get("text") or ""
            if isinstance(val, list):
                text = "\n".join(map(str, val))
            else:
                text = str(val)
        except ValueError:
            text = content or ""
        lines = []
        for raw in text.splitlines():
            if raw.strip():
                lines.append(OcrLine(raw.strip(), (0, 0, 0, 0), 1.0))
        return lines
