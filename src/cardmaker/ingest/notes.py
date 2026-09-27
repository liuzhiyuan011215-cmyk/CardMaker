"""读书笔记解析：把划线笔记拆成一条条，再按条分组交给模型（而不是按页）。

解析器是可插拔的：每种格式一个函数，detect_format 自动识别。
- Kindle 的 My Clippings.txt（英文、中文设备语言都支持）
- 通用格式：按空行分条，自动去掉日期时间行（复制来的网页笔记大多是这样）
- RIDI（리디북스）：需要真实样本才能写专门的解析器，在那之前按通用格式处理
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .pdf import METHOD_TEXT, DocumentText, PageText

KINDLE_SEPARATOR = "=========="
PER_PAGE = 8  # 每次交给模型几条划线


@dataclass
class Highlight:
    text: str
    book: str = ""
    author: str = ""
    chapter: str = ""
    location: str = ""
    note: str = ""  # 读者在这条划线旁写的想法


# ---------------------------------------------------------------------- 识别格式
def detect_format(text: str) -> str:
    if text.count(KINDLE_SEPARATOR) >= 1 and re.search(r"(Your Highlight|您在.*的标注|Your Note|的笔记)", text):
        return "kindle"
    return "generic"


FORMAT_NAMES = {"kindle": "Kindle 笔记（My Clippings）", "generic": "通用格式（按空行分条）"}


# ---------------------------------------------------------------------- Kindle
_KINDLE_META = re.compile(r"^-\s*(.*)$")
_KINDLE_LOCATION = re.compile(r"(?:Location|位置\s*#?)\s*([\d\-–]+)", re.I)
_KINDLE_PAGE = re.compile(r"(?:page|第)\s*(\d+)", re.I)


def _kindle_kind(meta: str) -> str:
    if re.search(r"Bookmark|书签", meta, re.I):
        return "bookmark"
    if re.search(r"Your Note|的笔记", meta, re.I):
        return "note"
    return "highlight"


def parse_kindle(text: str) -> list[Highlight]:
    highlights: list[Highlight] = []
    for block in text.replace("﻿", "").split(KINDLE_SEPARATOR):
        lines = [line.strip() for line in block.strip().splitlines()]
        if len(lines) < 2 or not _KINDLE_META.match(lines[1]):
            continue
        title, meta = lines[0], lines[1]
        match = re.match(r"^(.*?)\s*[(（]([^()（）]*)[)）]\s*$", title)  # 「书名 (作者)」
        book, author = (match.group(1), match.group(2)) if match else (title, "")
        content = " ".join(line for line in lines[2:] if line).strip()
        kind = _kindle_kind(meta)
        location = (_KINDLE_LOCATION.search(meta) or _KINDLE_PAGE.search(meta))
        location = location.group(1) if location else ""
        if kind == "bookmark" or not content:
            continue
        if kind == "note":  # 读者写的想法：附到同一本书、位置相近的上一条划线上
            if highlights and highlights[-1].book == book:
                highlights[-1].note = content
            continue
        highlights.append(Highlight(text=content, book=book, author=author, location=location))
    return highlights


# ---------------------------------------------------------------------- 通用
_DATE_TIME_LINE = re.compile(
    r"^\s*("
    r"\d{4}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}\s*[日.]?"  # 2024-03-03 / 2024.3.3 / 2024年3月3日 / 2024. 3. 3.
    r"|\d{1,2}:\d{2}(:\d{2})?"  # 21:15
    r"|上午|下午|오전|오후|AM|PM"
    r"|[（(]?[월화수목금토일一二三四五六日天]요?일?[)）]?|星期[一二三四五六日天]|周[一二三四五六日天]"
    r"|[\s,.]"
    r")+$", re.I)


def is_date_line(line: str) -> bool:
    return bool(line.strip()) and bool(_DATE_TIME_LINE.match(line)) and any(ch.isdigit() for ch in line)


def parse_generic(text: str) -> list[Highlight]:
    """有空行就按空行分条（一条划线可能占好几行）；没有空行就每行一条。自动去掉日期时间行。"""
    text = text.replace("﻿", "").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    highlights = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip() and not is_date_line(line)]
        content = " ".join(lines).strip()
        if len(content) >= 4:
            highlights.append(Highlight(text=content))
    return highlights


PARSERS = {"kindle": parse_kindle, "generic": parse_generic}


def parse_notes(text: str, fmt: str | None = None) -> tuple[str, list[Highlight]]:
    fmt = fmt or detect_format(text)
    return fmt, PARSERS[fmt](text)


def books_in(highlights: list[Highlight]) -> list[tuple[str, int]]:
    """笔记里有哪些书（Kindle 的一个文件里通常有很多本），按条数从多到少。"""
    return Counter(h.book for h in highlights if h.book).most_common()


# ---------------------------------------------------------------------- 变成「页」
def notes_to_document(path: Path, highlights: list[Highlight], title: str, per_page: int = PER_PAGE) -> DocumentText:
    """每 per_page 条划线组成一「页」交给模型；每条前面加编号，后面附章节和读者的想法。"""
    doc = DocumentText(path=path, title=title, page_count=0)
    for start in range(0, len(highlights), per_page):
        group = highlights[start:start + per_page]
        lines = []
        for i, h in enumerate(group, start + 1):
            extra = "".join(f"（{label}：{value}）" for label, value in
                            (("章节", h.chapter), ("位置", h.location), ("读者的想法", h.note)) if value)
            lines.append(f"[{i}] {h.text}{extra}")
        chapters = [h.chapter for h in group if h.chapter]
        doc.pages.append(PageText(index=len(doc.pages), label=f"第 {start + 1}–{start + len(group)} 条",
                                  text="\n".join(lines), method=METHOD_TEXT,
                                  chapter=chapters[0] if chapters else ""))
    doc.page_count = len(doc.pages)
    return doc


def load_notes(path: Path, book: str | None = None, title: str | None = None) -> DocumentText:
    text = path.read_text(encoding="utf-8-sig")
    _, highlights = parse_notes(text)
    if book:
        highlights = [h for h in highlights if h.book == book]
    books = books_in(highlights)
    title = title or book or (books[0][0] if len(books) == 1 else path.stem)
    return notes_to_document(path, highlights, title)
