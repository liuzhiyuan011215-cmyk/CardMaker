"""PDF → 每页文字。优先用文字层（快、准、免费）；没有文字层或文字层是乱码时才走 OCR。输出按段落整理（layout.py）。"""
from __future__ import annotations

import logging
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf

from ..progress import EXTRACT, ProgressEvent, ProgressFn, check_cancel
from .layout import ocr_text, page_text
from .ocr import OcrEngine, create_ocr_engine, lines_to_text

log = logging.getLogger(__name__)

METHOD_TEXT, METHOD_OCR, METHOD_BLANK = "文字层", "OCR", "空白页"


@dataclass
class PageText:
    index: int  # 从 0 开始的物理页序号
    label: str  # 显示用页码：优先用 PDF 自带的页码标签，否则为 index+1
    text: str
    method: str
    chapter: str = ""


@dataclass
class DocumentText:
    path: Path
    title: str
    page_count: int
    pages: list[PageText] = field(default_factory=list)


@dataclass
class PdfInfo:
    page_count: int
    title: str
    kind: str  # 文字版 / 扫描版 / 混合


def assess_text(text: str) -> str:
    """判断文字层是否可用：'ok' / 'empty' / 'garbled'。

    有些中韩文 PDF 的字体没有 Unicode 映射，提取出来是替换符、私用区字符或控制字符，
    这种情况也要走 OCR。
    """
    chars = [ch for ch in text if not ch.isspace()]
    if len(chars) < 10:
        return "empty"
    bad = sum(1 for ch in chars
              if ch == "�" or unicodedata.category(ch) in ("Cc", "Co", "Cs", "Cn"))
    return "garbled" if bad / len(chars) > 0.05 else "ok"


def inspect_pdf(path: Path, sample_pages: int = 5) -> PdfInfo:
    """导入时快速看一眼：页数、标题、是文字版还是扫描版（只抽查前几页，不做 OCR）。"""
    doc = pymupdf.open(path)
    try:
        title = (doc.metadata or {}).get("title") or path.stem
        sample = [doc[i] for i in range(min(sample_pages, doc.page_count))]
        text_ok = sum(1 for page in sample if assess_text(page.get_text("text")) == "ok")
        if text_ok == len(sample):
            kind = "文字版"
        elif text_ok == 0:
            kind = "扫描版"
        else:
            kind = "混合"
        return PdfInfo(page_count=doc.page_count, title=title, kind=kind)
    finally:
        doc.close()


def chapter_lookup(doc: pymupdf.Document) -> Callable[[int], str]:
    """根据 PDF 书签（目录）返回「页 → 所在章节」的查询函数。"""
    entries = sorted((page, title.strip()) for level, title, page in doc.get_toc(simple=True)
                     if level <= 2 and page >= 1)

    def lookup(page_no: int) -> str:  # page_no 从 1 开始
        current = ""
        for start, title in entries:
            if start > page_no:
                break
            current = title
        return current

    return lookup


def render_page(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()


def extract_pdf(path: Path, pages: list[int] | None = None, ocr_mode: str = "auto",
                ocr_langs: list[str] | tuple[str, ...] = ("zh", "ko"), dpi: int = 200,
                ocr_engine: OcrEngine | None = None, progress: ProgressFn | None = None,
                cancel: threading.Event | None = None) -> DocumentText:
    """提取 PDF 文字。

    pages：要处理的页码（从 1 开始），None 表示全部。
    ocr_mode：auto = 文字层不可用时 OCR；always = 全部 OCR；never = 从不 OCR。
    cancel：用户点取消时被置位，会抛出 Cancelled。
    """
    say = progress or (lambda event: None)
    doc = pymupdf.open(path)
    try:
        title = (doc.metadata or {}).get("title") or path.stem
        result = DocumentText(path=path, title=title, page_count=doc.page_count)
        chapter_of = chapter_lookup(doc)
        wanted = [p for p in (pages or range(1, doc.page_count + 1)) if 1 <= p <= doc.page_count]

        for n, page_no in enumerate(wanted, 1):
            check_cancel(cancel)
            page = doc[page_no - 1]
            label = page.get_label() or str(page_no)
            text = page.get_text("text")
            quality = assess_text(text)
            need_ocr = ocr_mode == "always" or (ocr_mode == "auto" and quality != "ok")

            if need_ocr and quality == "empty" and ocr_mode == "auto" and not page.get_images():
                method, text = METHOD_BLANK, ""  # 没字也没图，真的是空白页
            elif need_ocr:
                if ocr_engine is None:
                    say(ProgressEvent(EXTRACT, n - 1, len(wanted), "正在加载文字识别模型（首次使用会自动下载，约 30MB）…"))
                    ocr_engine = create_ocr_engine(ocr_langs)
                say(ProgressEvent(EXTRACT, n - 1, len(wanted), f"第 {label} 页是扫描页，正在识别文字…"))
                image = render_page(page, dpi)
                lines = ocr_engine.recognize(image)
                if ocr_engine.name == "api":
                    text = lines_to_text(lines)
                else:
                    text = ocr_text(lines, image.shape[1])
                method = METHOD_OCR
                if quality == "garbled":
                    log.info("第 %d 页文字层是乱码，已改用 OCR", page_no)
            else:
                method = METHOD_TEXT
                text = page_text(page)  # 一行一行 → 一段一段（只改空白，不改字）

            result.pages.append(PageText(index=page_no - 1, label=label, text=text.strip(),
                                         method=method, chapter=chapter_of(page_no)))
            say(ProgressEvent(EXTRACT, n, len(wanted), f"第 {label} 页：{method}，{len(text.strip())} 字"))
        return result
    finally:
        doc.close()


def read_text_file(path: Path) -> DocumentText:
    """纯文本当作只有一页的文档（读书笔记解析器在第五阶段接入）。"""
    text = path.read_text(encoding="utf-8-sig")
    return DocumentText(path=path, title=path.stem, page_count=1,
                        pages=[PageText(index=0, label="1", text=text.strip(), method=METHOD_TEXT)])
