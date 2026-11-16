"""用词典核对单词卡释义（假词典 + 假模型，不联网、不花 token）。"""
import json
import threading

from dataclasses import replace

import pytest

from cardmaker.dictionary import WIKTIONARY_NAME, DictError, Entry, Sense, supports
from cardmaker.pipeline import meaning_check
from cardmaker.pipeline.meaning_check import (check_meanings, matches, pick_senses, real_typos,
                                              settle_examples)
from cardmaker.pipeline.result import Card
from cardmaker.pipeline.runner import GenerateOptions, load_result, run_generation
from cardmaker.storage import TaskStore
from cardmaker.templates.loader import load_template
from conftest import FakeLLM

BAE = [Entry("1", "배", 1, "명사", [Sense(1, "사람이나 동물의 몸에서 내장이 들어 있는 곳.", "肚子", "人或动物的腹部。")]),
       Entry("2", "배", 2, "명사", [Sense(1, "사람이나 짐을 싣고 물 위를 다니는 교통수단.", "船", "水上交通工具。")]),
       Entry("3", "배", 3, "명사", [Sense(1, "배나무의 열매.", "梨", "梨树的果实。")])]
WORDS = {
    "배": BAE,
    "학교": [Entry("4", "학교", 0, "명사", [Sense(1, "학생을 가르치는 기관.", "学校", "教育学生的机构。")])],
    "사과": [Entry("5", "사과", 1, "명사", [Sense(1, "사과나무의 열매.", "苹果", "苹果树的果实。")]),
             Entry("6", "사과", 2, "명사", [Sense(1, "잘못을 인정하고 용서를 빎.", "道歉", "承认错误并请求原谅。")])],
}


class FakeDictionary:
    """默认像韩国语基础词典：只查韩语单词、只有中英日译词；everything=True 时像维基词典，什么词都查。"""

    def __init__(self, words=WORDS, fail=False, everything=False):
        self.words, self.fail, self.everything, self.calls = words, fail, everything, []
        self.view_examples = {}

    def covers(self, word, meaning_language="中文"):
        return self.everything or (any("가" <= ch <= "힣" for ch in word) and supports(meaning_language))

    def lookup(self, word, meaning_language="中文"):
        self.calls.append(word)
        if self.fail:
            raise DictError("词典 Key 不正确或还没生效，请在「设置」里检查")
        return self.words.get(word, [])

    def example(self, word, target_code, order, meaning_language="中文"):
        return self.view_examples.get((target_code, order), "")


@pytest.fixture
def template():
    return load_template("vocab_reading")


def card(template, word, meaning, sentence="", kind="word"):
    data = {"word": word, "surface": word, "kind": kind, "pos": "名", "meaning": meaning,
            "source_sentence": sentence}
    return Card(template.render_card_fields(data, {"source": "测试"}), data, "1", context={"source": "测试"})


def choices(*pairs):
    return json.dumps({"choices": [{"id": i, "sense": s} for i, s in pairs]})


def test_matches_glosses():
    pear = Sense(1, "", "梨", "梨树的果实。")
    assert matches("梨", pear) and matches("梨子", pear) and matches("梨（水果）", pear)
    assert matches("肚子；腹部", Sense(1, "", "肚子"))
    assert not matches("果实", pear)  # 只出现在解释里不算
    assert not matches("打开", Sense(1, "", "关，盖，合", "把打开的东西关上。"))  # 真实发生过：反义词被当成对上了
    assert not matches("船", pear) and not matches("苹果", pear)


def test_single_sense_word_is_verified_without_asking_the_model(template):
    cards = [card(template, "학교", "学校", "학교에 간다.")]
    llm = FakeLLM([])
    stats = check_meanings(cards, template, FakeDictionary(), llm)
    assert stats == {"verified": 1} and llm.calls == []
    assert cards[0].context["dict_check"]["status"] == "verified"


def _non_direct(words=WORDS):
    return {k: [replace(e, source="其他词典") for e in es] for k, es in words.items()}


def test_wrong_sense_is_corrected_from_the_dictionary(template):
    # 直查词典对不上释义时，不调用模型，直接换成词典的第一个义项
    cards = [card(template, "배", "手机", "배를 깎아 먹었다."), card(template, "배", "梨", "배가 달고 맛있다.")]
    llm = FakeLLM([])
    stats = check_meanings(cards, template, FakeDictionary(), llm)
    assert stats == {"corrected": 1, "verified": 1} and llm.calls == []
    fixed = cards[0]
    assert fixed.data["meaning"] == "肚子" and fixed.fields["Meaning"] == "名：肚子"
    assert fixed.context["dict_check"]["original"] == "手机" and fixed.selected


def test_no_matching_sense_unchecks_the_card(template):
    words = {"사과": [Entry("5", "사과", 1, "명사", [Sense(1, "사과나무의 열매.")])]}
    cards = [card(template, "사과", "苹果", "사과를 샀다.")]
    llm = FakeLLM([])
    stats = check_meanings(cards, template, FakeDictionary(words), llm)
    assert stats == {"unchecked": 1} and llm.calls == []
    assert not cards[0].selected and cards[0].context["dict_check"]["status"] == "unchecked"


def test_unknown_words_phrases_and_non_korean_words(template):
    cards = [card(template, "지어낸단어", "编的", "…"), card(template, "눈이 높다", "眼光高", "…", kind="phrase"),
             card(template, "alleviate", "减轻", "…")]
    dictionary = FakeDictionary()
    stats = check_meanings(cards, template, dictionary, FakeLLM([]))
    assert stats == {"not_found": 1, "unchecked": 2} and dictionary.calls == ["지어낸단어", "눈이 높다"]  # 英文单词这里查不了
    assert [c.context["dict_check"]["status"] for c in cards] == ["not_found", "unchecked", "unchecked"]
    assert not any(c.selected for c in cards)  # 没核对过的卡不能默认进 Anki


def test_phrases_found_in_the_dictionary_are_checked_like_words(template):
    words = {"눈이 높다": [Entry("9", "눈이 높다", 0, "구", [Sense(1, "보는 수준이 높다.", "眼光高")])]}
    cards = [card(template, "눈이 높다", "眼光高", "그는 눈이 높다.", kind="phrase")]
    assert check_meanings(cards, template, FakeDictionary(words), FakeLLM([])) == {"verified": 1}
    assert cards[0].selected


def test_cancelled_check_leaves_nothing_selected(template):
    cancel = threading.Event()
    cancel.set()
    cards = [card(template, "학교", "学校", "학교에 간다.")]
    assert check_meanings(cards, template, FakeDictionary(), FakeLLM([]), cancel=cancel) == {"unchecked": 1}
    assert not cards[0].selected


def test_dictionary_failure_does_not_break_generation(template):
    cards = [card(template, "배", "梨", "배를 먹었다."), card(template, "학교", "学校", "학교에 간다.")]
    stats = check_meanings(cards, template, FakeDictionary(fail=True), FakeLLM([]))
    assert stats == {"error": 2}
    assert all(not c.selected and c.context["dict_check"]["status"] == "error" for c in cards)


def test_model_failure_falls_back_to_literal_match(template):
    cards = [card(template, "배", "梨", "배를 먹었다."), card(template, "사과", "香蕉", "사과를 먹었다.")]
    stats = check_meanings(cards, template, FakeDictionary(_non_direct()), FakeLLM(["不是 JSON"]))
    assert stats == {"verified": 1, "unmatched": 1}
    assert cards[0].context["dict_check"]["note"] == "未经语境确认"
    assert cards[0].selected and not cards[1].selected  # 字面对上词典的留下，对不上的不能默认进 Anki


def test_other_meaning_languages_are_held_for_review(template):
    cards = [card(template, "배", "과일의 하나", "배를 먹었다.")]
    assert check_meanings(cards, template, FakeDictionary(), FakeLLM([]), meaning_language="法语") == {"unchecked": 1}
    assert not cards[0].selected


def test_generation_with_dictionary_saves_check_results(make_pdf, template):
    sentence = "배를 깎아 먹었다."
    pdf = make_pdf([f"{sentence} 공유 경제가 빠르게 확산되고 있다."])
    item = {"word": "배", "surface": "배", "kind": "word", "pos": "名", "meaning": "手机", "difficulty": "TOPIK 1级",
            "source_sentence": sentence}
    llm = FakeLLM([json.dumps({"items": [item]}, ensure_ascii=False)])
    store = TaskStore()
    result = run_generation(pdf, template, {}, llm, GenerateOptions(min_chars=5), store=store,
                            dictionary=FakeDictionary())
    assert result.cards[0].data["meaning"] == "肚子"
    assert any("词典核对释义：已按词典更正 1" in line for line in result.summary_lines())
    # 核对时直查词典不调用模型：生成 1 次 + 配例句 1 次，每次 150
    assert result.check_usage.calls == 1 and result.usage.total_tokens == 300
    assert "其中词典核对：150 token，调用 1 次" in result.summary_text()
    reloaded, _ = load_result(store, result.task_id)  # 存进数据库，历史记录里也看得到
    assert reloaded.cards[0].context["dict_check"]["status"] == "corrected"
    assert reloaded.usage.total_tokens == store.get_task(result.task_id).total_tokens == 300


def test_cross_language_meanings_are_judged_against_the_dictionary_sense(template):
    # 韩国人学英语：维基词典只有英语释义，字面比不了韩语释义 → 先选义项，再请模型对照这个义项判断
    bank = [Entry("", "bank", 0, "noun", [Sense(1, "An institution where one can place and borrow money."),
                                          Sense(2, "The edge of a river, lake, or other watercourse.")],
                  source=WIKTIONARY_NAME)]
    cards = [card(template, "bank", "은행", "I put money in the bank."),
             card(template, "bank", "은행", "We sat on the bank of the river.")]
    verdicts = json.dumps({"results": [{"id": 0, "same": True}, {"id": 1, "same": False, "fix": "강둑"}]},
                          ensure_ascii=False)
    llm = FakeLLM([choices((0, 1), (1, 2)), verdicts])
    words = {"bank": bank, "강둑": [Entry("7", "강둑", 0, "명사", [Sense(1, "강의 둑.", "bank; riverbank")])]}
    stats = check_meanings(cards, template, FakeDictionary(words, everything=True), llm, "韩语")
    assert stats == {"verified": 1, "corrected": 1} and all(c.selected for c in cards)
    fixed = cards[1]
    assert fixed.data["meaning"] == "강둑" and fixed.context["dict_check"]["original"] == "은행"
    assert fixed.context["dict_check"]["source"] == WIKTIONARY_NAME
    judge_prompt = llm.calls[1][1]["content"]
    assert "The edge of a river" in judge_prompt and "은행" in judge_prompt  # 只拿选中的义项来对照
    assert "은행" not in llm.calls[0][1]["content"]  # 选义项时看不到卡片上的释义


def test_later_homographs_are_not_crowded_out(monkeypatch):
    monkeypatch.setattr(meaning_check, "MAX_SENSES", 3)
    money = Entry("", "bank", 0, "noun", [Sense(1, "institution"), Sense(2, "branch"), Sense(3, "storage")])
    river = Entry("", "bank", 0, "noun", [Sense(1, "edge of a river")])
    assert [s.definition for _, s in pick_senses([money, river])] == ["institution", "branch", "edge of a river"]


def test_local_models_only_compare_literally(template):
    # 本地小模型选义项常选错：不请它选，也不让它改释义，对不上的只标出来
    class LocalLLM(FakeLLM):
        local = True

    cards = [card(template, "배", "船", "배를 깎아 먹었다."), card(template, "학교", "学校", "학교에 간다.")]
    llm = LocalLLM([])
    stats = check_meanings(cards, template, FakeDictionary(_non_direct()), llm)
    assert stats == {"verified": 2} and llm.calls == []
    assert cards[0].data["meaning"] == "船" and cards[0].context["dict_check"]["note"] == "未经语境确认"


# ---------------------------------------------------------------------- 释义定下来之后配例句、复查
def replies(*results):
    return [json.dumps({"results": r}) for r in results]


def test_wrong_meaning_is_replaced_then_dictionary_example_is_used(template):
    """AI 把 배 写成「手机」：词典改掉释义，例句用选中义项的词典例句，模型只负责翻译。"""
    words = {"배": [Entry("2", "배", 2, "명사", [Sense(1, "배를 타다.", "船", example="배를 타고 섬에 갔다.")]),
                    Entry("3", "배", 3, "명사", [Sense(1, "배나무의 열매.", "梨", example="배가 달다.")])]}
    cards = [card(template, "배", "手机", "배가 항구에 도착했다.")]
    llm = FakeLLM(replies([{"id": 0, "translation": "坐船去了岛上。"}]))
    check_meanings(cards, template, FakeDictionary(words), llm)
    settle_examples(cards, template, FakeDictionary(words), llm)
    fixed = cards[0]
    assert fixed.data["meaning"] == "船" and fixed.data["example"] == "배를 타고 섬에 갔다."
    assert fixed.data["example_translation"] == "坐船去了岛上。" and fixed.selected
    assert fixed.fields["ExampleLabel"] == "词典例句" and "dictionary_sense" in llm.calls[0][1]["content"]


def test_model_writes_the_example_from_the_settled_meaning_when_dictionary_has_none(template):
    cards = [card(template, "학교", "学校", "학교에 갔다.")]
    cards[0].context["dict_check"] = {"status": "verified", "sense": "학교 学校"}
    llm = FakeLLM(replies([{"id": 0, "example": "학교가 가깝다.", "translation": "学校很近。"}]))
    settle_examples(cards, template, FakeDictionary(), llm)
    assert cards[0].data["example"] == "학교가 가깝다." and cards[0].fields["ExampleLabel"] == "AI 例句"
    assert '"meaning": "学校"' in llm.calls[0][1]["content"]


def test_krdict_example_comes_from_the_detail_lookup(template):
    dictionary = FakeDictionary()
    dictionary.view_examples[("4", 1)] = "학교에 다닌다."
    cards = [card(template, "학교", "学校", "학교에 갔다.")]
    cards[0].context["dict_check"] = {"status": "verified", "target_code": "4", "sense_order": 1}
    settle_examples(cards, template, dictionary, FakeLLM(replies([{"id": 0, "translation": "上学。"}])))
    assert cards[0].data["example"] == "학교에 다닌다."


def test_dictionary_example_without_the_word_is_ignored(template):
    cards = [card(template, "학교", "学校", "학교에 갔다.")]
    cards[0].context["dict_check"] = {"status": "verified", "example": "아무 상관 없는 문장."}
    llm = FakeLLM(replies([{"id": 0, "example": "학교가 크다.", "translation": "学校大。"}]))
    settle_examples(cards, template, FakeDictionary(), llm)
    assert cards[0].data["example"] == "학교가 크다."


def test_book_example_is_kept_without_calling_the_model():
    book = load_template("vocab_book")
    data = {"word": "postpone", "pos": "v.", "meaning": "연기하다", "book_example": "The event was postponed."}
    cards = [Card(book.render_card_fields(data, {"source": "测试"}), data, "1", context={"source": "测试"})]
    llm = FakeLLM([])
    settle_examples(cards, book, FakeDictionary(everything=True), llm)
    assert not llm.calls and cards[0].fields["ExampleLabel"] == "教材例句" and cards[0].selected


def test_examples_are_skipped_when_the_option_is_off(template):
    cards = [card(template, "학교", "学校", "학교에 갔다.")]
    llm = FakeLLM([])
    settle_examples(cards, template, FakeDictionary(), llm, params={"ai_example": False})
    assert "example" not in cards[0].data and not llm.calls


def test_extraction_prompt_no_longer_asks_for_examples(template):
    fmt = template.output_format(template.resolve_params({"ai_example": True}))
    assert '"example"' not in fmt and '"ai_example"' not in load_template("vocab_book").output_format({})


def test_word_missing_from_the_dictionary_is_reported_and_unchecked(template):
    """OCR 把 book 认成 buuk：词典查不到，取消勾选并说明原因，不让模型猜。"""
    cards = [card(template, "buuk", "书", "I read a buuk.")]
    llm = FakeLLM([])
    check_meanings(cards, template, FakeDictionary({}, everything=True), llm)
    check = cards[0].context["dict_check"]
    assert not cards[0].selected and check["status"] == "not_found" and "OCR" in check["note"] and not llm.calls


def test_korean_meanings_are_replaced_with_the_dictionary_definition(template):
    # 韩语→韩语：释义说法不同但意思对就保留；意思不对时直接用词典原话，不用模型写的 fix
    words = {"절감하다": [Entry("1", "절감하다", 1, "동사", [Sense(1, "매우 크고 강하게 느끼다.", native=True)], source="其他词典"),
                      Entry("2", "절감하다", 2, "동사", [Sense(1, "아껴서 줄이다.", native=True)], source="其他词典")]}
    sentence = "유지 비용을 절감할 수 있다."
    cards = [card(template, "절감하다", "비용을 줄여서 아끼다.", sentence),
             card(template, "절감하다", "손잡이를 잡다.", sentence)]
    verdicts = json.dumps({"results": [{"id": 0, "same": True}, {"id": 1, "same": False, "fix": "모델이 쓴 말"}]},
                          ensure_ascii=False)
    llm = FakeLLM([choices((0, 2), (1, 2)), verdicts])
    assert check_meanings(cards, template, FakeDictionary(words), llm, "韩语") == {"verified": 1, "corrected": 1}
    assert cards[0].data["meaning"] == "비용을 줄여서 아끼다."
    assert cards[1].data["meaning"] == "아껴서 줄이다." and cards[1].context["dict_check"]["original"] == "손잡이를 잡다."


def test_examples_in_the_meaning_language_are_not_translated(template):
    cards = [card(template, "방치되다", "내버려 두어지다.", "차는 주차장에 방치되기 마련이다."),
             card(template, "완화하다", "덜하게 하다.", "체증을 완화하는 데 도움이 된다.")]
    cards[0].context["dict_check"] = {"status": "verified", "example": "건물들이 몇 달째 방치되어 있다."}
    written = json.dumps({"results": [{"id": 1, "example": "통증을 완화하는 약이다.", "translation": "통증을 완화하는 약이다."}]},
                         ensure_ascii=False)
    llm = FakeLLM([written])
    settle_examples(cards, template, FakeDictionary(), llm, "韩语")
    assert len(llm.calls) == 1 and "방치되다" not in llm.calls[0][1]["content"]  # 词典例句不用再问模型
    assert cards[0].data["example_translation"] == "" and cards[0].data["example_source"] == "dict"
    assert cards[1].data["example"] == "통증을 완화하는 약이다." and cards[1].data["example_translation"] == ""


def test_ai_translated_meaning_must_pass_a_reverse_lookup(template):
    # 真实发生过：postpone 被 AI 译成「연루하다」（被牵连）还被勾选
    postpone = [Entry("", "postpone", 0, "verb", [Sense(1, "To delay or put off an event.")], source=WIKTIONARY_NAME)]
    words = {"postpone": postpone,
             "연기하다": [Entry("1", "연기하다", 1, "동사", [Sense(1, "미루다.", "defer; postpone")])],
             "연루하다": [Entry("2", "연루하다", 0, "동사", [Sense(1, "휘말리다.", "be an accomplice; be involved")])]}

    def run(fix):
        cards = [card(template, "postpone", "취소하다", "The event was postponed.")]
        verdict = json.dumps({"results": [{"id": 0, "same": False, "fix": fix}]}, ensure_ascii=False)
        check_meanings(cards, template, FakeDictionary(words, everything=True), FakeLLM([choices((0, 1)), verdict]), "韩语")
        return cards[0]

    good, bad = run("연기하다"), run("연루하다")
    assert good.selected and good.context["dict_check"]["status"] == "corrected"
    assert not bad.selected and bad.context["dict_check"]["status"] == "unmatched"
    assert bad.context["dict_check"]["original"] == "취소하다"  # 用户能同时看到原释义和 AI 译的


def test_ai_translated_meaning_cannot_be_confirmed_in_other_languages(template):
    bank = [Entry("", "bank", 0, "noun", [Sense(1, "The edge of a river.")], source=WIKTIONARY_NAME)]
    cards = [card(template, "bank", "钱", "We sat on the bank.")]
    verdict = json.dumps({"results": [{"id": 0, "same": False, "fix": "河岸"}]}, ensure_ascii=False)
    check_meanings(cards, template, FakeDictionary({"bank": bank}, everything=True), FakeLLM([choices((0, 1)), verdict]), "中文")
    assert not cards[0].selected and cards[0].data["meaning"] == "河岸"


def test_only_real_character_errors_count_as_ocr_typos():
    for noise in ("@→[动]", "③→", "1→/", "]→□", "園→□", "0017→0177", "올해연세가→올해 연세가", "市 场→市场", "@→; 1→/"):
        assert real_typos(noise) == "", noise
    assert real_typos("빛기→빚기") == "빛기→빚기"
    assert real_typos("@→; 늦게일어나서→늦게 일어나서; 아晋니다→아픕니다") == "아晋니다→아픕니다"


def test_word_without_a_sentence_is_verified_when_the_meaning_is_a_real_sense(template):
    near = {"가깝다": [Entry("1", "가깝다", 0, "형용사", [Sense(1, "거리가 짧다.", "近"), Sense(2, "친하다.", "亲近，密切")])]}
    hit, miss = card(template, "가깝다", "近"), card(template, "가깝다", "手机")
    llm = FakeLLM([])
    assert check_meanings([hit, miss], template, FakeDictionary(near), llm) == {"verified": 1, "corrected": 1}
    assert hit.selected and miss.selected and miss.data["meaning"] == "近" and llm.calls == []  # 直查词典直接更正，不问模型
