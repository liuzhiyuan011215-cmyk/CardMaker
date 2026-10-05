"""版面整理：把 PDF 文字层或 OCR 的「一行一行」还原成「一段一段」。

借鉴 opendataloader-pdf 的长处（段落、标题、列表、表格行），但守住一条底线：只改空白，不改字。
提取出来的字一个不动，只是把段落内部的换行接上（中日文直接接，英文、韩文补一个空格），
同一行里的几个单元格用制表符连起来，段与段之间换行。原文校验本来就忽略空白，所以这一步不影响
防编造；好处是大模型拿到的是完整的句子和完整的表格行，抄原句时不会断在行尾，单词和释义也不会错位。

判断「下一行是不是同一段」用的都是版面线索：行距有没有变大、上一行有没有写满、
下一行是不是以项目符号或编号开头、字号和粗细有没有变、有没有缩进变化。拿不准就不合并——
不合并只是保持原来按行输出的样子，合并错了才会把标题和正文搅在一起。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float  # 字号（文字层）或文字框高度（OCR），只用来做相对比较
    bold: bool = False


# 行首出现这些就是新的一段：项目符号、各种编号、「第…章/条」、Q1.
LIST_MARKER = re.compile(
    r"\s*(?:[•·▪▸▹►▶◆◇■□●○◦‣※★☆✓✔→]"
    r"|[-–—*]\s"
    r"|[①-⑳⑴-⒇⒈-⒛㉠-㉻㈀-㈜]"
    r"|[(（]?\d{1,3}[.)．、）](?!\d)"
    r"|[A-Za-z가-힣][.)](?=\s)"
    r"|[一二三四五六七八九十]{1,3}[、.．]"
    r"|第[一二三四五六七八九十百千\d]+[章节節条條項项款编編]"
    r"|Q\d+[.:．])"
)

OPENERS = set("([{“‘（［｛〔【《〈「『")  # 后面不加空格
CLOSERS = set(")]}”’）］｝〕】》〉」』,.;:!?，。、；：！？…%")  # 前面不加空格
QUOTES = set("“”‘’")


def is_cjk(ch: str) -> bool:
    """中日文字符和全角标点：它们之间本来就没有空格。韩文不算（韩文按词加空格）。"""
    cp = ord(ch)
    return (0x2E80 <= cp <= 0x2FDF or 0x3000 <= cp <= 0x30FF or 0x3400 <= cp <= 0x4DBF
            or 0x4E00 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF or 0xFF00 <= cp <= 0xFF60
            or 0x20000 <= cp <= 0x3134F)


def is_cjk_punct(ch: str) -> bool:
    cp = ord(ch)
    return ch in QUOTES or 0x3000 <= cp <= 0x303F or (0xFF00 <= cp <= 0xFF60 and not ch.isalnum())


def _last_token(text: str) -> str:
    parts = text.split()
    return parts[-1] if parts else ""


def looks_like_url(token: str) -> bool:
    return "://" in token or token.startswith("www.") or "@" in token


def needs_space(prev: str, nxt: str) -> bool:
    """把上一行和下一行接起来时，中间要不要补一个空格。"""
    if not prev or not nxt:
        return False
    a, b = prev[-1], nxt[0]
    if a.isspace() or b.isspace() or a in OPENERS or b in CLOSERS:
        return False
    if is_cjk(a) or is_cjk(b):
        return False
    if a in "-/" or looks_like_url(_last_token(prev)):
        return False  # 连字符、网址在行尾断开：原样接上（连字符保留，不删字）
    return True


_SPACE_RUN = re.compile(r"[ \t　]+")


def tidy_spaces(text: str) -> str:
    """去掉中文标点和汉字之间多出来的空格（PDF 字距造成的，例如「以“ 是否查出舞弊” 的」）。

    只去掉一边是中文标点、另一边是中日文字符的空格：「第一章 总则」这种汉字之间的空格、
    英文和韩文旁边的空格都不动。
    """
    def repl(m: re.Match) -> str:
        start, end = m.start(), m.end()
        if start == 0 or end == len(text):
            return m.group(0)
        a, b = text[start - 1], text[end]
        a_cjk, b_cjk = is_cjk(a) or a in QUOTES, is_cjk(b) or b in QUOTES
        if (is_cjk_punct(a) and b_cjk) or (is_cjk_punct(b) and a_cjk):
            return ""
        return m.group(0)

    return _SPACE_RUN.sub(repl, text)


def _char_width(ch: str, size: float) -> float:
    if is_cjk(ch) or "가" <= ch <= "힣":
        return size
    return size * (0.3 if ch.isspace() else 0.55)


def first_word_width(line: Line) -> float:
    """下一行第一个「放不下才会换行」的单位有多宽：中日文是一个字，英文、韩文是一个词。"""
    text = line.text.lstrip()
    if not text:
        return 0.0
    if is_cjk(text[0]):
        return _char_width(text[0], line.size)
    word = re.match(r"[^\s　-〿一-鿿]+", text)
    return sum(_char_width(ch, line.size) for ch in (word.group(0) if word else text[0]))


def merge_rows(lines: list[Line]) -> list[Line]:
    """同一行里左右分开的几块（表格的单元格、词条和释义）用制表符连成一行。"""
    rows: list[Line] = []
    for line in lines:
        if rows:
            last = rows[-1]
            overlap = min(last.y1, line.y1) - max(last.y0, line.y0)
            if overlap >= 0.5 * min(last.y1 - last.y0, line.y1 - line.y0) and line.x0 >= last.x1:
                rows[-1] = Line(f"{last.text.rstrip()}\t{line.text.lstrip()}", last.x0, min(last.y0, line.y0),
                                line.x1, max(last.y1, line.y1), max(last.size, line.size), last.bold and line.bold)
                continue
        rows.append(line)
    return rows


def _right_edges(lines: list[Line], page_width: float) -> list[float]:
    """每行所在栏的右边界。有分栏时左栏用左栏最长的行；否则按左右页边距对称估计（宁大勿小）。"""
    from .ocr.rapid_fusion import find_column_gutter

    left = min(ln.x0 for ln in lines)
    page_right = max(max(ln.x1 for ln in lines), page_width - left)
    gutter = find_column_gutter([(ln.x0, ln.y0, ln.x1, ln.y1) for ln in lines], int(page_width))
    if gutter is None:
        return [page_right] * len(lines)
    left_right = max((ln.x1 for ln in lines if ln.x1 <= gutter), default=page_right)
    return [left_right if ln.x1 <= gutter else page_right for ln in lines]


def continues(prev: Line, cur: Line, para: list[Line], typical_gap: float, right_edge: float) -> bool:
    """cur 是不是 prev 所在段落的下一行。"""
    size = max(prev.size, cur.size, 1.0)
    if "\t" in prev.text or "\t" in cur.text:
        return False  # 表格行各占一行
    if cur.y0 <= prev.y0 + 0.3 * size or cur.x0 >= prev.x1 or prev.x0 >= cur.x1:
        return False  # 没有往下走（换栏了），或者左右不重叠
    if cur.y0 - prev.y1 > typical_gap + 0.35 * size:
        return False  # 行距明显变大：段间距
    if LIST_MARKER.match(cur.text) or prev.bold != cur.bold or abs(prev.size - cur.size) > 0.12 * size:
        return False  # 新的列表项，或者字号、粗细变了（标题）
    max_indent = 4 * size if LIST_MARKER.match(para[0].text) else 1.5 * size  # 列表项的悬挂缩进可以更深
    if cur.x0 > prev.x0 + max_indent:
        return False  # 首行缩进：新的一段
    if len(para) > 1 and cur.x0 < prev.x0 - 0.5 * size:
        return False  # 悬挂缩进结束了
    # 上一行剩下的空间放得下下一行的第一个字（词），说明是主动换行，段落到此结束
    return right_edge - prev.x1 <= first_word_width(cur) + 0.5 * size


def build_paragraphs(lines: list[Line], page_width: float) -> list[str]:
    """按阅读顺序给出的行 → 段落列表。只改空白：把所有空白去掉后，结果和输入完全一样。"""
    lines = merge_rows([ln for ln in lines if ln.text.strip()])
    if not lines:
        return []
    gaps = [b.y0 - a.y1 for a, b in zip(lines, lines[1:]) if b.y0 > a.y0]
    typical_gap = statistics.median(gaps) if gaps else 0.0
    edges = _right_edges(lines, page_width)
    paras: list[list[int]] = [[0]]
    for i in range(1, len(lines)):
        prev, cur = lines[i - 1], lines[i]
        if continues(prev, cur, [lines[k] for k in paras[-1]], typical_gap, edges[i - 1]):
            paras[-1].append(i)
        else:
            paras.append([i])
    out = []
    for para in paras:
        text = ""
        for k in para:
            piece = lines[k].text.strip()
            text = f"{text} {piece}" if text and needs_space(text, piece) else text + piece
        out.append(tidy_spaces(text))
    return out


# ---------------------------------------------------------------------- 两种来源
def page_lines(page) -> list[Line]:
    """PyMuPDF 页面 → 文字行（顺序和 get_text("text") 一样，即 PDF 里的书写顺序）。"""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"])
            visible = [span for span in line["spans"] if span["text"].strip()]
            if not visible:
                continue
            size = max(visible, key=lambda s: len(s["text"].strip()))["size"]
            bold = all(span["flags"] & 16 or "bold" in span["font"].lower() for span in visible)
            lines.append(Line(text, *line["bbox"], size=size, bold=bold))
    return lines


def page_text(page) -> str:
    return "\n".join(build_paragraphs(page_lines(page), page.rect.width))


def ocr_text(ocr_lines, image_width: int) -> str:
    """OCR 结果（带文字框）→ 按段落排好的文字。"""
    lines = [Line(ln.text, *ln.box, size=ln.box[3] - ln.box[1]) for ln in ocr_lines if ln.text.strip()]
    return "\n".join(build_paragraphs(lines, image_width))
