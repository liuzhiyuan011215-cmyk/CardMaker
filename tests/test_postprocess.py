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
