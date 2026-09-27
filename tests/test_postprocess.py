import unicodedata

from cardmaker.pipeline.postprocess import APPROX, EXACT, check_grounding, dedup_key, locate, normalize

PAGE = ("최근 몇 년 사이 우리 사회에서는 공유 경제가 빠르게\n확산되고 있다. "
        "차는 주차장에 방치되기 마련이다.\nThe surge in parcel deliveries has intensified congestion.")


def test_normalize_handles_width_case_space_and_decomposed_hangul():
    decomposed = unicodedata.normalize("NFD", "한국어")
    assert normalize(decomposed) == normalize("한국어")
    assert normalize("ＡＢＣ  def") == "abcdef"


def test_locate_exact_across_line_break():
    assert locate("공유 경제가 빠르게 확산되고 있다.", PAGE) == EXACT


def test_locate_approx_tolerates_ocr_typo():
    assert locate("차는 주차장에 방치되기 마련이디.", PAGE) == APPROX  # 一个字不同


def test_locate_rejects_invented_sentence():
    assert locate("공유 경제는 앞으로 더욱 발전할 것으로 전망된다.", PAGE) is None


def test_locate_short_value_requires_exact():
    assert locate("사회", PAGE) == EXACT
    assert locate("사과", PAGE) is None


def test_check_grounding_combines_fields():
    item = {"word": "방치되다", "source_sentence": "차는 주차장에 방치되기 마련이다.", "book_example": ""}
    assert check_grounding(item, ["source_sentence", "book_example"], PAGE) == EXACT
    item["source_sentence"] = "완전히 지어낸 문장입니다 여기에는 없어요."
    assert check_grounding(item, ["source_sentence"], PAGE) is None


def test_dedup_key_ignores_case_width_and_space():
    assert dedup_key({"word": " Alleviate "}, "word") == dedup_key({"word": "alleviate"}, "word")


# ---------------------------------------------------------------------- 填空卡、选择题
from cardmaker.pipeline.postprocess import add_mcq_options, cloze_answers  # noqa: E402

LAW = "第三条 本科生每人最多可同时借阅图书十册，借期为三十日。"


def test_cloze_answers():
    assert cloze_answers("借期为{{c1::三十日}}，最多{{c2::十册::数量}}") == ["三十日", "十册"]


def test_cloze_answers_must_come_from_source():
    good = {"text": "本科生借期为{{c1::三十日}}。", "quote": LAW}
    invented = {"text": "本科生借期为{{c1::四十五日}}。", "quote": LAW}
    no_blank = {"text": "本科生借期为三十日。", "quote": LAW}
    assert check_grounding(good, ["quote"], LAW, ["text"]) == EXACT
    assert check_grounding(invented, ["quote"], LAW, ["text"]) is None  # 编造的答案
    assert check_grounding(no_blank, ["quote"], LAW, ["text"]) is None  # 没有挖空


def test_mcq_options_shuffled_but_answer_correct():
    item = {"question": "本科生借期是多久？", "correct": "三十日", "distractors": ["十五日", "六十日", "九十日"]}
    result = add_mcq_options(item)
    options = [result[f"option_{x}"] for x in "abcd"]
    assert sorted(options) == sorted(["三十日", "十五日", "六十日", "九十日"])
    assert result[f"option_{result['answer'].lower()}"] == "三十日"
    assert add_mcq_options(item) == result  # 同一道题每次顺序一样
    answers = {add_mcq_options({**item, "question": f"第{i}题"})["answer"] for i in range(30)}
    assert len(answers) >= 3  # 正确答案不会总在同一个位置
