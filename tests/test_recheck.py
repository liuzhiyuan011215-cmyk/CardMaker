"""「待确认」的卡交给大模型再认一次：结果还要过词典，通过才勾选。"""
import json

import pytest
from conftest import FakeLLM

from cardmaker.dictionary import Entry, Sense
from cardmaker.ingest.pdf import METHOD_OCR, METHOD_TEXT
from cardmaker.llm.client import FatalLLMError, Usage
from cardmaker.pipeline import recheck
from cardmaker.pipeline.meaning_check import flag_ocr_suspects, pending_cards
from cardmaker.pipeline.recheck import recheck_cards
from cardmaker.pipeline.result import Card
from cardmaker.storage import Chunk, ChunkRecord
from cardmaker.templates import load_template

WORDS = {"postpone": [Entry("", "postpone", 0, "verb", [Sense(1, "To delay.", "연기하다")])]}


class FakeDictionary:
    def __init__(self, words=WORDS):
        self.words = words

    def covers(self, word, meaning_language="中文"):
        return True

    def lookup(self, word, meaning_language="中文"):
        return self.words.get(word, [])

    def example(self, word, target_code, order, meaning_language="中文"):
        return ""


@pytest.fixture
def template():
    return load_template("vocab_book")


def typo_card(template, word="postpane", meaning="연기하다", kind=None):
    data = {"word": word, "pos": "동", "meaning": meaning, "book_example": f"The event was {word}d.", "ocr_suspect": f"{word}→postpone"}
    if kind:
        data["kind"] = kind
    return Card(template.render_card_fields(data, {"source": "书"}), data, "1", context={"source": "书"})


def records(method=METHOD_OCR):
    return [ChunkRecord(0, Chunk("1", "", "postpane 연기하다 The event was postpaned.", "", method, ""))]


def read(word, sentence=""):
    return json.dumps({"results": [{"id": 0, "word": word, "sentence": sentence}]})


def run(cards, template, llm, monkeypatch, recs=None, dictionary=None, usage=None):
    monkeypatch.setattr(recheck, "page_image", lambda path, label: b"png")
    flag_ocr_suspects(cards)
    return recheck_cards(cards, template, recs if recs is not None else records(), "书.pdf", llm,
                         dictionary or FakeDictionary(), "韩语", usage=usage)


def test_corrected_word_must_pass_the_dictionary_before_it_is_selected(template, monkeypatch):
    card = typo_card(template)
    llm = FakeLLM([read("postpone", "The event was postponed.")])
    usage = Usage()
    assert run([card], template, llm, monkeypatch, usage=usage) == 1
    assert card.selected and card.data["word"] == "postpone" and card.data["book_example"] == "The event was postponed."
    assert card.fields["Front"] == "postpone" and "postpane→postpone" in card.context["dict_check"]["note"]
    assert not pending_cards([card]) and usage.calls == 1
    parts = llm.calls[0][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")  # 扫描页带上图片


def test_word_still_missing_from_dictionary_stays_pending(template, monkeypatch):
    card = typo_card(template)
    assert run([card], template, FakeLLM([read("postpane")]), monkeypatch) == 0
    assert pending_cards([card]) and card.context["dict_check"]["status"] == "not_found"


def test_model_cannot_swap_in_a_different_word(template, monkeypatch):
    card = typo_card(template)
    words = {"table": [Entry("", "table", 0, "noun", [Sense(1, "A table.", "연기하다")])]}
    assert run([card], template, FakeLLM([read("table")]), monkeypatch, dictionary=FakeDictionary(words)) == 0
    assert card.data["word"] == "postpane" and pending_cards([card])


def test_phrases_are_not_sent_to_the_model(template, monkeypatch):
    template_reading = load_template("vocab_reading")
    data = {"word": "in turn", "surface": "in turn", "kind": "phrase", "meaning": "轮流", "source_sentence": "We spoke in turn."}
    card = Card(template_reading.render_card_fields(data, {"source": "书"}), data, "1", selected=False,
                context={"source": "书", "dict_check": {"status": "unchecked"}})
    llm = FakeLLM([])
    assert recheck_cards([card], template_reading, records(), "书.pdf", llm, FakeDictionary(), "韩语") == 0
    assert llm.calls == [] and pending_cards([card])


def test_falls_back_to_text_when_the_model_cannot_read_images(template, monkeypatch):
    card = typo_card(template)
    llm = FakeLLM([RuntimeError("image not supported"), read("postpone")])
    assert run([card], template, llm, monkeypatch) == 1
    assert isinstance(llm.calls[1][1]["content"], str)


def test_text_pages_do_not_send_images(template, monkeypatch):
    card = typo_card(template)
    llm = FakeLLM([read("postpone")])
    assert run([card], template, llm, monkeypatch, recs=records(METHOD_TEXT)) == 1
    assert isinstance(llm.calls[0][1]["content"], str)


def test_without_dictionary_nothing_is_released(template):
    card = typo_card(template)
    flag_ocr_suspects([card])
    assert recheck_cards([card], template, records(), "书.pdf", FakeLLM([]), None) == 0
    assert pending_cards([card]) and card.data["ocr_suspect"]


def test_bad_key_is_reported(template, monkeypatch):
    card = typo_card(template)
    with pytest.raises(FatalLLMError):
        run([card], template, FakeLLM([FatalLLMError("Key 不对")]), monkeypatch, recs=records(METHOD_TEXT))
    assert pending_cards([card])  # 卡片还留在待确认里


def test_only_pages_with_pending_cards_are_read(template, monkeypatch):
    good = typo_card(template, "postpone")
    bad = typo_card(template)
    good.data["ocr_suspect"] = ""
    good.page_label = "9"
    llm = FakeLLM([read("postpone")])
    recs = [*records(), ChunkRecord(1, Chunk("9", "", "另一页", "", METHOD_OCR, ""))]
    good.context["dict_check"] = {"status": "verified"}
    run([good, bad], template, llm, monkeypatch, recs=recs)
    assert len(llm.calls) == 1 and good.selected
