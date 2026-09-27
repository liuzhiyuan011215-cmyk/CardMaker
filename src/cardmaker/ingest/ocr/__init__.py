from .base import OcrEngine, OcrLine, lines_to_text


def create_ocr_engine(langs: list[str] | tuple[str, ...] = ("zh", "ko")) -> OcrEngine:
    """创建默认 OCR 引擎。以后换引擎只改这里。"""
    from .rapid_fusion import RapidFusionOCR

    return RapidFusionOCR(langs)


__all__ = ["OcrEngine", "OcrLine", "lines_to_text", "create_ocr_engine"]
