"""新词典方案：ECDICT（英→中）、韩语版维基词典（英→韩、中→韩）。不联网；字面对不上时才请模型选义项（这里用假模型）。"""
import json
import sqlite3

import pytest

from cardmaker.dictionary import Dictionaries, EcdictClient, KoWiktionaryClient, PanlexClient
from cardmaker.pipeline.meaning_check import check_meanings
from cardmaker.pipeline.result import Card
from cardmaker.templates.loader import load_template
from conftest import FakeLLM

KO_ROWS = [  # 韩语维基本地库：(单词的语言, 单词, kaikki 的一行 JSON)
    ("en", "abatement", {"word": "abatement", "pos": "noun", "senses": [{"glosses": ["감소, 경감."]}, {"glosses": ["감액, 감가."]}]}),
    ("zh", "学习", {"word": "学习", "pos": "unknown", "senses": [{"tags": ["no-gloss"]}], "redirects": ["學習"]}),
    ("zh", "學習", {"word": "學習", "pos": "verb", "senses": [{"glosses": ["학습하다, 공부하다."]}]}),
]
PANLEX_ROWS = [("adaptive", "적응하는", 3, 10), ("adaptive", "개작", 1, 50), ("减少", "줄이다", 2, 40)]


@pytest.fixture
def dictionary(tmp_path):
    db = sqlite3.connect(tmp_path / "ecdict.sqlite")
    db.execute("CREATE TABLE ecdict (key TEXT, word TEXT, translation TEXT)")
    db.executemany("INSERT INTO ecdict VALUES (?, ?, ?)", [
        ("abatement", "abatement", "n. 减少, 减轻, 缓和\\n[化] 废料; 失效"),
        ("abhor", "abhor", "vt. 憎恶, 痛恨"),
        ("adaptive", "adaptive", "a. 适应的, 能适应的")])
    db.commit()
    ko = sqlite3.connect(tmp_path / "kowiktionary.sqlite")
    ko.execute("CREATE TABLE kowikt (lang TEXT, word TEXT, json TEXT)")
    ko.executemany("INSERT INTO kowikt VALUES (?, ?, ?)", [(l, w, json.dumps(j, ensure_ascii=False)) for l, w, j in KO_ROWS])
    ko.commit()
    pl = sqlite3.connect(tmp_path / "panlex_ko.sqlite")
    pl.execute("CREATE TABLE panlex_pairs (key TEXT, kor TEXT, shared_meanings INTEGER, kor_meanings INTEGER)")
    pl.executemany("INSERT INTO panlex_pairs VALUES (?, ?, ?, ?)", PANLEX_ROWS)
    pl.commit()
    return Dictionaries(None, None, EcdictClient(tmp_path / "ecdict.sqlite"),
                        KoWiktionaryClient(tmp_path / "kowiktionary.sqlite"), PanlexClient(tmp_path / "panlex_ko.sqlite"))


def card(template, word, meaning):
    data = {"word": word, "pos": "n.", "meaning": meaning, "book_example": ""}
    return Card(template.render_card_fields(data, {"source": "测试"}), data, "1", context={"source": "测试"})


def run(dictionary, language, *pairs, choices=None):
    """choices：字面对不上的卡依次让模型选的义项编号（0 = 都不符合）；不给就是一次都不该调用模型。"""
    template = load_template("vocab_book")
    cards = [card(template, word, meaning) for word, meaning in pairs]
    reply = json.dumps({"choices": [{"id": i, "sense": n} for i, n in enumerate(choices or [])]})
    llm = FakeLLM([reply] if choices else [])
    check_meanings(cards, template, dictionary, llm, language)
    assert len(llm.calls) == (1 if choices else 0)
    return cards


def test_english_word_chinese_meaning_uses_ecdict(dictionary):
    ok, replaced, typo = run(dictionary, "中文", ("abatement", "减少，减轻(the act or process of abating)"),
                             ("abhor", "喜欢"), ("abatemcnt", "减少"), choices=[0])  # 模型：「喜欢」不是任何义项
    assert ok.selected and ok.context["dict_check"]["status"] == "verified" and "减轻" in ok.data["meaning"]
    assert replaced.selected and replaced.data["meaning"] == "憎恶, 痛恨"  # 意思不对：换成第一个义项，原释义留着
    assert replaced.context["dict_check"]["status"] == "corrected" and replaced.context["dict_check"]["original"] == "喜欢"
    assert not typo.selected and typo.context["dict_check"]["status"] == "not_found"  # OCR 认错的词查不到


def test_english_word_korean_meaning_uses_korean_wiktionary_then_panlex(dictionary):
    found, fallback, missing = run(dictionary, "韩语", ("abatement", "减少，减轻"), ("adaptive", "适应的"), ("abhor", "憎恶"),
                                   choices=[1, 1])  # 意思对，但释义是中文
    assert found.selected and found.data["meaning"] == "감소, 경감"  # 书上是中文，换成韩语词典的第一个义项
    assert fallback.selected and fallback.data["meaning"] == "적응하는"  # 韩语维基没收：PanLex 兜底，来源最多的译词
    assert fallback.context["dict_check"]["source"] == "PanLex"
    assert not missing.selected and missing.data["meaning"] == "憎恶"  # 两本都没收，单词没错：待确认
    assert missing.context["dict_check"]["status"] == "unchecked"


def test_chinese_word_korean_meaning_follows_traditional_form(dictionary):
    (word,) = run(dictionary, "韩语", ("学习", "공부하다"))
    assert word.selected and word.context["dict_check"]["status"] == "verified"


def test_chinese_word_with_ocr_spaces_falls_back_to_panlex(dictionary):
    (word,) = run(dictionary, "韩语", ("减 少", "감소하다"), choices=[1])  # OCR 在汉字之间插了空格；韩语维基没收「减少」
    assert word.context["dict_check"]["source"] == "PanLex"
    # 模型认定「감소하다」就是「줄이다」这个意思：说法不同也算对，保留原来的写法
    assert word.selected and word.context["dict_check"]["status"] == "verified" and word.data["meaning"] == "감소하다"


def test_english_meanings_are_compared_as_whole_words():
    from cardmaker.dictionary import Sense
    from cardmaker.pipeline.meaning_check import matches
    sense = lambda text: Sense(1, text, trans_word=text)
    assert not matches("to advance, to bring forward", sense("to delay; to postpone"))  # 只有 to 一样
    assert not matches("willing, eager", sense("reluctantly; in a forced manner; unwilling"))  # in、willing 是别的词的一部分
    assert matches("to put off", sense("to delay; to postpone; to put off"))
    assert matches("postpone", sense("to delay; to postpone"))
    assert matches("减少，减轻", sense("减少, 减轻, 缓和"))  # 中文照旧
