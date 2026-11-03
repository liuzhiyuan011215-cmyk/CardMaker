import json

import pytest

from cardmaker.templates.loader import (OutputError, TemplateError, find_term, highlight,
                                        list_templates, load_template)


@pytest.fixture
def reading():
    return load_template("vocab_reading")


def test_builtin_templates_load():
    ids = {t.id for t in list_templates()}
    assert {"vocab_reading", "vocab_book"} <= ids


def test_unknown_template_raises():
    with pytest.raises(TemplateError):
        load_template("no_such_template")


def test_params_defaults_and_coercion(reading):
    params = reading.resolve_params({"level": "雅思 6分", "ai_example": "false", "max_per_page": "8"})
    assert params["level"] == "雅思 6分"
    assert params["ai_example"] is False
    assert params["max_per_page"] == 8
    assert params["meaning_language"] == "中文"  # 默认值


def test_output_format_skips_disabled_fields(reading):
    on = reading.output_format(reading.resolve_params({}))
    off = reading.output_format(reading.resolve_params({"ai_example": False, "source_translation": False}))
    assert '"example"' not in on and '"example"' not in off  # 例句等释义定下来才配，提取时不要
    assert '"source_translation"' not in off
    assert "json" in on  # 百炼等平台的 JSON 模式要求提示词里出现 json


def test_build_messages_contains_page_text(reading):
    params = reading.resolve_params({"level": "TOPIK 4级"})
    messages = reading.build_messages({**params, "text": "본문 내용", "context": "", "page_label": "37",
                                       "chapter": "1장", "book_title": "책"})
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "TOPIK 4级" in user and "본문 내용" in user and "第 37 页" in user and "1장" in user


def test_parse_output_valid_and_normalizes(reading):
    content = json.dumps({"items": [{
        "word": " 방치되다 ", "kind": "WORD", "meaning": "被搁置", "source_sentence": "차는 방치되기 마련이다.",
        "difficulty": 5, "pos": None,
    }]}, ensure_ascii=False)
    items, errors = reading.parse_output(content)
    assert errors == []
    item = items[0]
    assert item["word"] == "방치되다"  # 去掉首尾空白
    assert item["kind"] == "word"  # 枚举大小写修正
    assert item["difficulty"] == "5"  # 数字转字符串
    assert item["pos"] == ""  # null 转默认值


def test_parse_output_drops_few_bad_items(reading):
    good = {"word": "a", "meaning": "b", "source_sentence": "c"}
    content = json.dumps({"items": [good, good, {"word": "only word"}]})
    items, errors = reading.parse_output(content)
    assert len(items) == 2 and len(errors) == 1


@pytest.mark.parametrize("content", [
    "不是 JSON",
    '{"other": 1}',
    '{"items": [{"word": "x"}, {"word": "y"}]}',  # 大多数条目缺必填字段 → 整体重试
])
def test_parse_output_rejects(reading, content):
    with pytest.raises(OutputError):
        reading.parse_output(content)


def test_card_fields_mapping(reading):
    item = {"word": "방치되다", "surface": "방치되기", "pos": "自", "meaning": "被搁置",
            "source_sentence": "차는 주차장에 방치되기 마련이다.", "source_translation": "",
            "example": "빈집이 오래 방치되어 있었다.", "example_translation": "空房子被闲置了很久。"}
    fields = reading.render_card_fields(item, {"source": "《书》 · p.37"})
    assert fields["Front"] == "방치되다"
    assert fields["Meaning"] == "自：被搁置"
    assert fields["SourceSentence"] == "차는 주차장에 <b>방치되기</b> 마련이다."
    assert fields["Example"] == "빈집이 오래 <b>방치되어</b> 있었다."  # 用词干找到变形
    assert fields["ExampleLabel"] == "AI 例句"
    assert fields["Source"] == "《书》 · p.37"


def test_card_fields_without_pos_and_escaping(reading):
    item = {"word": "a<b", "pos": "", "meaning": "x & y", "source_sentence": "s"}
    fields = reading.render_card_fields(item, {"source": ""})
    assert fields["Meaning"] == "x &amp; y"
    assert fields["Front"] == "a&lt;b"


def test_vocab_book_prefers_book_example():
    book = load_template("vocab_book")
    base = {"word": "합의", "pos": "名", "meaning": "协议"}
    with_book = book.render_card_fields({**base, "book_example": "합의에 이르렀다.", "book_example_translation": "达成了协议。",
                                         "ai_example": "", "ai_example_translation": ""}, {"source": ""})
    assert with_book["ExampleLabel"] == "教材例句"
    assert with_book["ExampleTranslation"] == "达成了协议。"
    with_ai = book.render_card_fields({**base, "book_example": "", "book_example_translation": "",
                                       "ai_example": "합의를 했다.", "ai_example_translation": "达成了一致。"}, {"source": ""})
    assert with_ai["ExampleLabel"] == "AI 例句"
    assert with_ai["Example"] == "<b>합의</b>를 했다."  # 名词原样找到时只加粗名词本身，不带助词


@pytest.mark.parametrize("text,term,expected", [
    ("We must alleviate the strain.", "alleviate", "alleviate"),
    ("It alleviated the strain.", "alleviate", "alleviated"),
    ("차는 방치되기 마련이다.", "방치되다", "방치되기"),
    ("nothing here", "absent", None),
])
def test_find_term(text, term, expected):
    start, end = find_term(text, term)
    assert (text[start:end] if start != -1 else None) == expected


def test_highlight_escapes_html():
    assert str(highlight("<i>x</i> word", "word")) == "&lt;i&gt;x&lt;/i&gt; <b>word</b>"


# ---------------------------------------------------------------------- 各领域模板
from cardmaker.templates.loader import list_groups  # noqa: E402


def test_all_templates_render():
    for tpl in list_templates():
        params = tpl.resolve_params({})
        messages = tpl.build_messages({**params, "text": "正文", "context": "", "page_label": "1",
                                       "chapter": "", "book_title": "书"})
        assert "正文" in messages[1]["content"] and "json" in messages[1]["content"], tpl.id


def test_groups_and_variants():
    groups = {g.id: [v.variant for v in g.variants] for g in list_groups()}
    assert groups["law"] == ["填空卡", "选择题"] and groups["notes"] == ["填空卡", "选择题"]
    assert list(groups)[:3] == ["vocab_reading", "vocab_book", "notes"]


def test_cloze_template_requires_blank():
    law = load_template("law_cloze")
    bad = json.dumps({"items": [{"text": "借期为三十日。", "quote": "借期为三十日。"}]}, ensure_ascii=False)
    with pytest.raises(OutputError):
        law.parse_output(bad)
    good = json.dumps({"items": [{"text": "借期为{{c1::三十日}}。", "quote": "借期为三十日。", "article": "第三条"}]},
                      ensure_ascii=False)
    items, _ = law.parse_output(good)
    fields = law.render_card_fields(items[0], {"source": "《规定》 · p.1"})
    assert fields["Text"] == "借期为{{c1::三十日}}。" and fields["Source"] == "《规定》 · p.1 · 第三条"


def test_mcq_template_requires_three_distractors():
    mcq = load_template("law_mcq")
    bad = json.dumps({"items": [{"question": "借期？", "correct": "三十日", "distractors": ["十日"], "quote": "q"}]},
                     ensure_ascii=False)
    with pytest.raises(OutputError):
        mcq.parse_output(bad)


def test_code_block_keeps_indentation():
    code = load_template("code_cloze")
    fields = code.render_card_fields({"text": "用 {{c1::sorted}} 排序", "code": "for x in nums:\n    print(x)",
                                      "quote": "q"}, {"source": ""})
    assert "<pre><code>for x in nums:\n    print(x)</code></pre>" in fields["Text"]
