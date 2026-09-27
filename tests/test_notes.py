from pathlib import Path

from cardmaker.ingest.notes import (books_in, detect_format, is_date_line, load_notes, notes_to_document,
                                    parse_generic, parse_kindle)

KINDLE_EN = """﻿思考的方法 (林远)
- Your Highlight on page 12 | Location 150-152 | Added on Monday, September 1, 2026 9:15:01 PM

真正的学习不是记住答案，而是能用自己的话讲给别人听。
==========
思考的方法 (林远)
- Your Note on page 12 | Location 152 | Added on Monday, September 1, 2026 9:16:00 PM

这就是费曼学习法
==========
思考的方法 (林远)
- Your Bookmark on page 30 | Location 380 | Added on Monday, September 1, 2026 9:20:00 PM


==========
时间的礼物 (陈默)
- Your Highlight on page 8 | Location 90-92 | Added on Tuesday, September 2, 2026 8:00:00 AM

我们高估了一天能做的事，却低估了一年能做的事。
==========
"""

KINDLE_ZH = """书与文明 (王某)
- 您在第 5 页（位置 #70-72）的标注 | 添加于 2026年9月1日星期一 下午9:15:01

活字可以反复排版使用，大大降低了印书的成本。
==========
"""


def test_kindle_english():
    assert detect_format(KINDLE_EN) == "kindle"
    highlights = parse_kindle(KINDLE_EN)
    assert [h.book for h in highlights] == ["思考的方法", "时间的礼物"]  # 书签被跳过
    assert highlights[0].author == "林远" and highlights[0].location == "150-152"
    assert highlights[0].note == "这就是费曼学习法"  # 读者的笔记附到划线上
    assert books_in(highlights) == [("思考的方法", 1), ("时间的礼物", 1)]


def test_kindle_chinese():
    assert detect_format(KINDLE_ZH) == "kindle"
    [h] = parse_kindle(KINDLE_ZH)
    assert h.book == "书与文明" and h.location == "70-72" and h.text.startswith("活字")


def test_generic_drops_date_lines():
    text = "2026.09.01 21:15\n第一条划线的内容，\n跨了两行。\n\n2026년 9월 1일\n오후 9:15\n두 번째 하이라이트입니다.\n\n短"
    assert detect_format(text) == "generic"
    assert [h.text for h in parse_generic(text)] == ["第一条划线的内容， 跨了两行。", "2026년 9월 1일 두 번째 하이라이트입니다."]


def test_generic_one_per_line_without_blank_lines():
    assert [h.text for h in parse_generic("第一条划线内容\n第二条划线内容")] == ["第一条划线内容", "第二条划线内容"]


def test_date_line_detection():
    for line in ("2026-09-01", "2026.9.1 21:15", "2026年9月1日 星期一", "下午9:15", "2026. 9. 1. 오후 9:15"):
        assert is_date_line(line), line
    for line in ("第一条划线的内容", "2026 年计划"):
        assert not is_date_line(line), line


def test_notes_become_numbered_groups(tmp_path):
    highlights = parse_kindle(KINDLE_EN)
    doc = notes_to_document(Path("x.txt"), highlights * 5, "书", per_page=4)
    assert doc.page_count == 3 and doc.pages[0].label == "第 1–4 条"
    assert doc.pages[0].text.splitlines()[0].startswith("[1] 真正的学习")
    assert "（读者的想法：这就是费曼学习法）" in doc.pages[0].text


def test_load_notes_filters_one_book(tmp_path):
    path = tmp_path / "My Clippings.txt"
    path.write_text(KINDLE_EN, encoding="utf-8")
    doc = load_notes(path, book="时间的礼物")
    assert doc.title == "时间的礼物" and "低估了一年" in doc.pages[0].text and "真正的学习" not in doc.pages[0].text
