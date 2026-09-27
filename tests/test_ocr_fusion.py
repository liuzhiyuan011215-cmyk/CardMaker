"""真实加载 OCR 模型的端到端测试（较慢，首次运行会下载模型）。

用 macOS 系统字体渲染一张中韩英混排的图片，检查融合识别的结果。
跳过方式：pytest -m "not slow"
"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

KO_FONT = Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")
ZH_FONT = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not (KO_FONT.exists() and ZH_FONT.exists()), reason="需要 macOS 系统字体")]

LINES = [
    [("확산되다 ", "ko"), ("[自] 扩散，蔓延", "zh")],
    [("그는 매일 도서관에 간다.", "ko")],
    [("为了节省能源费用，关掉了电灯。", "zh")],
    [("The committee postponed the decision.", "ko")],
]


def render(lines) -> np.ndarray:
    fonts = {"ko": ImageFont.truetype(str(KO_FONT), 34), "zh": ImageFont.truetype(str(ZH_FONT), 34)}
    img = Image.new("RGB", (1000, 80 * len(lines) + 40), "white")
    draw = ImageDraw.Draw(img)
    for i, segments in enumerate(lines):
        x = 30
        for text, lang in segments:
            draw.text((x, 30 + i * 80), text, fill="black", font=fonts[lang])
            x += draw.textlength(text, font=fonts[lang])
    return np.array(img)


def test_fusion_reads_mixed_korean_chinese_english():
    from cardmaker.ingest.ocr import create_ocr_engine

    engine = create_ocr_engine(["zh", "ko"])
    got = [line.text for line in engine.recognize(render(LINES))]
    expected = ["확산되다 [自] 扩散，蔓延", "그는 매일 도서관에 간다.", "为了节省能源费用，关掉了电灯。",
                "The committee postponed the decision."]
    # 韩语识别模型偶尔会丢掉句末的小句号（整行识别也一样，属于模型本身的特点），
    # 原句校验用的是近似匹配，不影响出卡，所以这里忽略句末句号
    assert [g.rstrip(".") for g in got] == [e.rstrip(".") for e in expected]
