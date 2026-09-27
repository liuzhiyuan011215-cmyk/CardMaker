"""按页分块，保留页码和章节。每块附带上一页末尾的一小段文字作为上下文。"""
from __future__ import annotations

from dataclasses import dataclass

from ..ingest.pdf import DocumentText, PageText


@dataclass
class Chunk:
    page_label: str
    chapter: str
    text: str
    context: str  # 上一页末尾，只帮助模型理解（跨页的句子、上一页的条款号），不从中出卡
    method: str  # 文字层 / OCR


def visible_len(text: str) -> int:
    return len("".join(text.split()))


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
                max_chars: int = 5000) -> tuple[list[Chunk], list[PageText]]:
    """返回 (要处理的块, 因为字太少而跳过的页)。"""
    chunks: list[Chunk] = []
    skipped: list[PageText] = []
    previous = ""
    for page in doc.pages:
        if visible_len(page.text) < min_chars:
            skipped.append(page)
            continue
        for part in split_long(page.text, max_chars):
            chunks.append(Chunk(page.label, page.chapter, part, tail(previous, context_chars), page.method))
            previous = part
    return chunks, skipped
