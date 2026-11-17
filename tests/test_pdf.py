import io

import numpy as np
import pymupdf
from PIL import Image

from cardmaker.ingest.ocr import OcrLine
from cardmaker.ingest.pdf import METHOD_BLANK, METHOD_OCR, METHOD_TEXT, assess_text, extract_pdf
from cardmaker.pipeline.chunking import make_chunks, split_long, tail


class FakeOCR:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def recognize(self, image: np.ndarray):
        self.calls += 1
        assert image.ndim == 3 and image.shape[2] == 3
        return [OcrLine("扫描页第一行", (0, 0, 10, 10), 0.99), OcrLine("스캔 페이지", (0, 20, 10, 30), 0.98)]


def test_assess_text():
    assert assess_text("  \n ") == "empty"
    assert assess_text("공유 경제가 빠르게 확산되고 있다.") == "ok"
    assert assess_text("��� abc 乱码乱码乱码") == "garbled"
    assert assess_text(" 正常文字只有几个") == "garbled"  # 私用区字符


def test_text_layer_with_toc_and_page_labels(make_pdf):
    path = make_pdf(["첫 번째 페이지입니다. 공유 경제가 확산되고 있다.", "두 번째 페이지입니다. 책임 소재가 불분명하다."],
                    toc=[[1, "1장 시작", 1], [1, "2장 과제", 2]], first_label=37)
    doc = extract_pdf(path, ocr_mode="never")
    assert [p.label for p in doc.pages] == ["37", "38"]
    assert [p.chapter for p in doc.pages] == ["1장 시작", "2장 과제"]
    assert all(p.method == METHOD_TEXT for p in doc.pages)
    assert "공유 경제" in doc.pages[0].text


def test_page_selection(make_pdf):
    path = make_pdf([f"第 {i} 页的内容，足够长的一段文字。" for i in range(1, 6)])
    doc = extract_pdf(path, pages=[2, 4, 99], ocr_mode="never")
    assert [p.index for p in doc.pages] == [1, 3]


def test_scanned_page_goes_to_ocr_and_blank_page_is_skipped(tmp_path):
    # 第 1 页：只有图片（模拟扫描件）；第 2 页：空白
    img = Image.new("RGB", (200, 100), "white")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 200, 100), stream=buf.getvalue())
    doc.new_page()
    path = tmp_path / "scan.pdf"; doc.save(path)

    ocr = FakeOCR()
    result = extract_pdf(path, ocr_engine=ocr, dpi=50)
    assert [p.method for p in result.pages] == [METHOD_OCR, METHOD_BLANK]
    assert result.pages[0].text == "扫描页第一行\n스캔 페이지"
    assert ocr.calls == 1


def test_force_ocr(make_pdf):
    path = make_pdf(["有文字层的页面，但用户选择全部 OCR。"])
    ocr = FakeOCR()
    result = extract_pdf(path, ocr_mode="always", ocr_engine=ocr, dpi=50)
    assert result.pages[0].method == METHOD_OCR and ocr.calls == 1


def test_chunks_skip_short_pages_and_carry_context(make_pdf):
    path = make_pdf(["第一页：" + "内容" * 30 + "上一页的结尾句。", "短", "第三页：" + "正文" * 30])
    doc = extract_pdf(path, ocr_mode="never")
    chunks = make_chunks(doc, min_chars=10, context_chars=20)
    assert [(c.page_label, c.skip_reason) for c in chunks] == [("1", ""), ("2", "字数太少"), ("3", "")]
    assert chunks[0].context == ""
    assert chunks[2].context.endswith("上一页的结尾句。")  # 跳过的页不影响上下文衔接


def test_tail_and_split_long():
    assert tail("short", 100) == "short"
    assert len(tail("word " * 100, 50)) <= 50
    parts = split_long("\n".join(["段落" * 20] * 10), 100)
    assert len(parts) > 1 and all(len(p) <= 100 for p in parts)


def test_chinese_word_list_is_not_skipped_and_blank_page_is_skipped(make_pdf):
    seven_words = "\n".join(f"{i}. {w}" for i, w in enumerate(["坚持", "珍惜", "尊重", "理解", "信任", "包容", "鼓励"], 1))
    doc = extract_pdf(make_pdf([seven_words, "   \n  "]), ocr_mode="never")
    chunks = make_chunks(doc)  # 默认 min_chars=30，7 个中文双字词+序号去空白 28 字，按中文 2 字算得 42 >= 30
    assert [(c.page_label, c.skip_reason) for c in chunks] == [("1", ""), ("2", "字数太少")]
