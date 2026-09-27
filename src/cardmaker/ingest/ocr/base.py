"""OCR 引擎接口。换 OCR 引擎只需要再实现一个 OcrEngine，其余代码不用改。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass
class OcrLine:
    text: str
    box: tuple[int, int, int, int]  # x0, y0, x1, y1（像素）
    score: float
    langs: list[str] = field(default_factory=list)  # 这一行里各段最终采用的语言模型


class OcrEngine(Protocol):
    name: str

    def recognize(self, image: np.ndarray) -> list[OcrLine]:
        """输入 RGB 图像（H×W×3），按阅读顺序返回文字行。"""
        ...


def lines_to_text(lines: list[OcrLine]) -> str:
    return "\n".join(line.text for line in lines if line.text.strip())
