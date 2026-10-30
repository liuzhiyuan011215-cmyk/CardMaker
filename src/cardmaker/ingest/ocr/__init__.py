import logging

from .base import OcrEngine, OcrLine, lines_to_text

log = logging.getLogger(__name__)


def create_ocr_engine(langs: list[str] | tuple[str, ...] = ("zh", "ko")) -> OcrEngine:
    """创建默认 OCR 引擎：Qota-OCR；模型下载不到（例如第一次用时没联网）就退回分段双模型融合。"""
    from .rapid_fusion import RapidFusionOCR

    try:
        from .qota import QotaOCR

        return QotaOCR()
    except Exception as exc:  # 下载、加载失败都退回，不让扫描件直接读不出来
        log.warning("Qota-OCR 不可用（%s），改用分段双模型融合", exc)
        return RapidFusionOCR(langs)


__all__ = ["OcrEngine", "OcrLine", "lines_to_text", "create_ocr_engine"]
