"""生成主流程：重试、失败隔离、原文校验、去重、数量上限（全部用假模型，不花 token）。"""
import json

import pytest

from cardmaker.pipeline import runner
from cardmaker.pipeline.runner import GenerateOptions, run_generation
from cardmaker.templates.loader import load_template
from conftest import FakeLLM

S1 = "공유 경제가 빠르게 확산되고 있다."
S2 = "차는 주차장에 방치되기 마련이다."
S3 = "책임 소재가 불분명한 경우가 적지 않다."
SHORT_PAGES = GenerateOptions(min_chars=5)  # 测试页很短，放宽「字太少就跳过」的门槛


def item(word, sentence, **extra):
    return {"word": word, "surface": word, "kind": "word", "pos": "自", "meaning": "释义",
            "difficulty": "TOPIK 5级", "source_sentence": sentence, **extra}


def reply(*items):
    return json.dumps({"items": list(items)}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)


@pytest.fixture
def template():
    return load_template("vocab_reading")


def test_invalid_output_is_retried_with_feedback(make_pdf, template):
    pdf = make_pdf([f"{S1} {S2}"])
    llm = FakeLLM(["这不是 JSON", reply(item("확산되다", S1), item("지어내다", "원문에 없는 지어낸 문장입니다."))])
    result = run_generation(pdf, template, {"level": "TOPIK 4级"}, llm, SHORT_PAGES)

    assert len(llm.calls) == 2
    retry_messages = llm.calls[1]
    assert retry_messages[-2]["role"] == "assistant" and "不符合要求" in retry_messages[-1]["content"]
    assert [c.fields["Front"] for c in result.cards] == ["확산되다"]
    assert result.dropped["原文中找不到"] == 1  # 编造的原句被丢弃
    assert result.chunks[0].status == "ok" and result.chunks[0].attempts == 2
    assert result.usage.calls == 2 and result.usage.prompt_tokens == 200


def test_failed_chunk_does_not_stop_the_task(make_pdf, template):
    pdf = make_pdf([f"第一页 {S1}", f"第二页 {S2}"])
    llm = FakeLLM(["坏输出", "坏输出", "坏输出", reply(item("방치되다", S2))])
    result = run_generation(pdf, template, {}, llm, GenerateOptions(max_attempts=3, min_chars=5))

    assert [c.status for c in result.chunks] == ["failed", "ok"]
    assert "输出不合格" in result.chunks[0].error
    assert [c.fields["Front"] for c in result.cards] == ["방치되다"]


def test_api_error_is_retried(make_pdf, template):
    pdf = make_pdf([S1 * 3])
    llm = FakeLLM([ConnectionError("网络断了"), reply(item("확산되다", S1))])
    result = run_generation(pdf, template, {}, llm, SHORT_PAGES)
    assert result.chunks[0].status == "ok" and len(result.cards) == 1


def test_dedup_keeps_first_occurrence(make_pdf, template):
    pdf = make_pdf([f"第一页 {S1}", f"第二页 {S1} {S3}"])
    llm = FakeLLM([reply(item("확산되다", S1)),
                   reply(item("확산되다", S1), item("불분명하다", S3))])
    result = run_generation(pdf, template, {}, llm, SHORT_PAGES)
    assert [(c.fields["Front"], c.page_label) for c in result.cards] == [("확산되다", "1"), ("불분명하다", "2")]
    assert result.dropped["重复"] == 1


def test_max_cards_stops_early(make_pdf, template):
    pdf = make_pdf([f"一 {S1} {S2}", f"二 {S3}", f"三 {S3}"])
    llm = FakeLLM([reply(item("확산되다", S1), item("방치되다", S2))])
    result = run_generation(pdf, template, {}, llm, GenerateOptions(max_cards=1, min_chars=5))
    assert len(result.cards) == 1
    assert result.stopped_early
    assert len(llm.calls) == 1  # 后面的页不再调用模型，省 token
    assert [c.status for c in result.chunks] == ["ok", "skipped", "skipped"]
    assert "提前结束" in result.summary_text()


def test_summary_mentions_counts(make_pdf, template):
    pdf = make_pdf([f"{S1} {S2}"])
    llm = FakeLLM([reply(item("확산되다", S1), item("-기 마련이다", S2, kind="phrase", pos="句型"))])
    result = run_generation(pdf, template, {"level": "TOPIK 4级"}, llm, SHORT_PAGES)
    text = result.summary_text()
    assert "卡片：2 张" in text and "单词 1" in text and "短语/句型 1" in text and "TOPIK 5级 2" in text
