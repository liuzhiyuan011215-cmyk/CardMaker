"""用词典核对单词卡释义（假词典 + 假模型，不联网、不花 token）。"""
import json

import pytest

from cardmaker.dictionary import WIKTIONARY_NAME, DictError, Entry, Sense, supports
from cardmaker.pipeline import meaning_check
from cardmaker.pipeline.meaning_check import check_meanings, matches, pick_senses
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

    def covers(self, word, meaning_language="中文"):
        return self.everything or (any("가" <= ch <= "힣" for ch in word) and supports(meaning_language))

    def lookup(self, word, meaning_language="中文"):
        self.calls.append(word)
        if self.fail:
            raise DictError("词典 Key 不正确或还没生效，请在「设置」里检查")
        return self.words.get(word, [])


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
    assert matches("果实", pear)  # 出现在译文释义里
    assert not matches("船", pear) and not matches("苹果", pear)


def test_single_sense_word_is_verified_without_asking_the_model(template):
    cards = [card(template, "학교", "学校", "학교에 간다.")]
    llm = FakeLLM([])
    stats = check_meanings(cards, template, FakeDictionary(), llm)
    assert stats == {"verified": 1} and llm.calls == []
    assert cards[0].context["dict_check"]["status"] == "verified"


def test_wrong_sense_is_corrected_from_the_dictionary(template):
    # 原句说的是吃梨，模型却写成「船」：模型只能在词典义项里选，选了 3（梨）→ 按词典更正
    cards = [card(template, "배", "船", "배를 깎아 먹었다."), card(template, "배", "梨", "배가 달고 맛있다.")]
    llm = FakeLLM([choices((0, 3), (1, 3))])
    stats = check_meanings(cards, template, FakeDictionary(), llm)
    assert stats == {"corrected": 1, "verified": 1}
    fixed = cards[0]
    assert fixed.data["meaning"] == "梨" and fixed.fields["Meaning"] == "名：梨"  # 卡片字段也跟着更新
    assert fixed.context["dict_check"]["original"] == "船" and fixed.selected
    prompt = llm.calls[0][1]["content"]
    assert "배를 깎아 먹었다." in prompt and "배나무의 열매." in prompt  # 原句和词典释义都交给了模型


def test_no_matching_sense_unchecks_the_card(template):
    cards = [card(template, "사과", "苹果手机", "사과를 샀다.")]
    stats = check_meanings(cards, template, FakeDictionary(), FakeLLM([choices((0, 0))]))
    assert stats == {"unmatched": 1}
    assert not cards[0].selected and cards[0].context["dict_check"]["deselected"]


def test_unknown_words_phrases_and_non_korean_words(template):
    cards = [card(template, "지어낸단어", "编的", "…"), card(template, "눈이 높다", "眼光高", "…", kind="phrase"),
             card(template, "alleviate", "减轻", "…")]
    dictionary = FakeDictionary()
    stats = check_meanings(cards, template, dictionary, FakeLLM([]))
    assert stats == {"not_found": 1} and dictionary.calls == ["지어낸단어"]  # 短语、英文单词不查
    assert "dict_check" not in cards[1].context and "dict_check" not in cards[2].context


def test_dictionary_failure_does_not_break_generation(template):
    cards = [card(template, "배", "梨", "배를 먹었다."), card(template, "학교", "学校", "학교에 간다.")]
    stats = check_meanings(cards, template, FakeDictionary(fail=True), FakeLLM([]))
    assert stats == {"error": 2}
    assert all(c.selected and c.context["dict_check"]["status"] == "error" for c in cards)


def test_model_failure_falls_back_to_literal_match(template):
    cards = [card(template, "배", "梨", "배를 먹었다."), card(template, "사과", "香蕉", "사과를 먹었다.")]
    stats = check_meanings(cards, template, FakeDictionary(), FakeLLM(["不是 JSON"]))
    assert stats == {"verified": 1, "unmatched": 1}
    assert cards[0].context["dict_check"]["note"] == "未经语境确认"
    assert cards[1].selected  # 没有经过模型确认，只提醒、不取消勾选


def test_other_meaning_languages_are_skipped(template):
    cards = [card(template, "배", "과일의 하나", "배를 먹었다.")]
    assert check_meanings(cards, template, FakeDictionary(), FakeLLM([]), meaning_language="韩语") == {}


def test_generation_with_dictionary_saves_check_results(make_pdf, template):
    sentence = "배를 깎아 먹었다."
    pdf = make_pdf([f"{sentence} 공유 경제가 빠르게 확산되고 있다."])
    item = {"word": "배", "surface": "배", "kind": "word", "pos": "名", "meaning": "船", "difficulty": "TOPIK 1级",
            "source_sentence": sentence}
    llm = FakeLLM([json.dumps({"items": [item]}, ensure_ascii=False), choices((0, 3))])
    store = TaskStore()
    result = run_generation(pdf, template, {}, llm, GenerateOptions(min_chars=5), store=store,
                            dictionary=FakeDictionary())
    assert result.cards[0].data["meaning"] == "梨"
    assert any("词典核对释义：已按词典更正 1" in line for line in result.summary_lines())
    # 核对时调用模型用的 token 也算进任务：生成 1 次 + 选义项 1 次，每次 150
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
    stats = check_meanings(cards, template, FakeDictionary({"bank": bank}, everything=True), llm, "韩语")
    assert stats == {"verified": 1, "corrected": 1}
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
    stats = check_meanings(cards, template, FakeDictionary(), llm)
    assert stats == {"verified": 2} and llm.calls == []
    assert cards[0].data["meaning"] == "船" and cards[0].context["dict_check"]["note"] == "未经语境确认"
