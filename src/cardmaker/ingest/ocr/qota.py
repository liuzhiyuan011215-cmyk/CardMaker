"""Qota-OCR 识别：一个模型就能读同一行里混排的韩文、中文、日文、英文。

模型：https://huggingface.co/atonlee/Qota-OCR（Apache-2.0，PP-OCRv6 small rec 加训韩文，ONNX 约 27 MB）。
文字行检测和阅读顺序沿用 RapidFusionOCR 的（RapidOCR 检测 + 双栏排序），只把识别换成 Qota。
在真实扫描的中韩单词书上，字错误率比分段双模型融合低一半以上，速度也快好几倍。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .base import OcrLine
from .rapid_fusion import RapidFusionOCR, join_segments, order_boxes

REPO = "atonlee/Qota-OCR"
REVISION = "b3e644a61b307b55d82839016576cbee03670d58"  # 固定版本：结果可复现，也不会被换掉


class QotaOCR(RapidFusionOCR):
    """继承只为复用 RapidFusionOCR._detect（检测框外扩）；识别全部换成 Qota。"""
    name = "qota"

    def __init__(self):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from rapidocr import RapidOCR

        model = hf_hub_download(REPO, "onnx/model.onnx", revision=REVISION)
        config = hf_hub_download(REPO, "preprocessor_config.json", revision=REVISION)
        self._chars = json.loads(Path(config).read_text(encoding="utf-8"))["character_list"]
        self._session = ort.InferenceSession(model)
        self._detector = RapidOCR(params={"Global.log_level": "warning"})

    def recognize(self, image: np.ndarray) -> list[OcrLine]:
        lines = []
        for row in order_boxes(self._detect(image), image.shape[1]):
            reads = [self._read(image[y0:y1, x0:x1]) for x0, y0, x1, y1 in row]
            if not any(text.strip() for text, _ in reads):
                continue
            box = (min(b[0] for b in row), min(b[1] for b in row), max(b[2] for b in row), max(b[3] for b in row))
            lines.append(OcrLine(text=join_segments([text for text, _ in reads]), box=box,
                                 score=float(np.mean([score for _, score in reads])), langs=["qota"]))
        return lines

    def _read(self, crop: np.ndarray) -> tuple[str, float]:
        """识别一行。按模型卡：BGR，高 48 保持宽高比，宽 320～3200、不足的右侧补 0，缩放到 [-1, 1]；CTC 解码。"""
        h, w = crop.shape[:2]
        width = min(3200, max(1, round(w * 48 / h)))
        line = np.asarray(Image.fromarray(crop).resize((width, 48), Image.BILINEAR), dtype=np.float32)
        x = np.zeros((1, 3, 48, max(320, width)), dtype=np.float32)
        x[0, :, :, :width] = (line[:, :, ::-1].transpose(2, 0, 1) / 255 - 0.5) / 0.5
        probs = self._session.run(None, {"x": x})[0][0]
        ids = probs.argmax(axis=-1)
        keep = (ids != 0) & np.r_[True, ids[1:] != ids[:-1]]  # 0 是空白；连续重复只算一个
        text = "".join(self._chars[i] for i in ids[keep])
        return text, float(probs.max(axis=-1)[keep].mean()) if keep.any() else 0.0
