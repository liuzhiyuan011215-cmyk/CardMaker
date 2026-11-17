"""按页分块，保留页码和章节。每块附带上一页末尾的一小段文字作为上下文。"""
from __future__ import annotations

from dataclasses import dataclass

from ..ingest.pdf import DocumentText


@dataclass
class Chunk:
    page_label: str
    chapter: str
    text: str
    context: str  # 上一页末尾，只帮助模型理解（跨页的句子、上一页的条款号），不从中出卡
    method: str  # 文字层 / OCR
    skip_reason: str = ""  # 非空表示这一页不处理（例如字太少）


def visible_len(text: str) -> int:
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        if "\u4e00" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7a3":
            total += 2
        else:
            total += 1
    return total


def tail(text: str, max_chars: int) -> str:
    """取末尾约 max_chars 个字符，尽量从一个完整的词或句子开始。"""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[-max_chars:]
    for sep in ("\n", "。", ". ", " "):
        pos = cut.find(sep)
        if 0 <= pos < max_chars // 2:
            return cut[pos + len(sep):].strip()
    return cut


def split_long(text: str, max_chars: int) -> list[str]:
    """一页文字过长时按段落切开（少见，比如很密的法律条文页）。"""
    if len(text) <= max_chars:
        return [text]
    parts, current = [], ""
    for para in text.split("\n"):
        if current and len(current) + len(para) + 1 > max_chars:
            parts.append(current)
            current = ""
        current = f"{current}\n{para}" if current else para
    if current:
        parts.append(current)
    return parts


def make_chunks(doc: DocumentText, min_chars: int = 30, context_chars: int = 200,
                max_chars: int = 5000) -> list[Chunk]:
    """按书中顺序返回所有分块；字太少的页也在列表里，但标了 skip_reason，不会交给模型。"""
    chunks: list[Chunk] = []
    previous = ""
    for page in doc.pages:
        if visible_len(page.text) < min_chars:
            chunks.append(Chunk(page.label, page.chapter, page.text, "", page.method, skip_reason="字数太少"))
            continue
        for part in split_long(page.text, max_chars):
            chunks.append(Chunk(page.label, page.chapter, part, tail(previous, context_chars), page.method))
            previous = part
    return chunks
