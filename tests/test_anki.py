"""写入 Anki：用假的 AnkiConnect 测试，不需要打开 Anki，也不会碰真实牌库。"""
import sqlite3
import zipfile

import pytest

from cardmaker.anki import (ADDED, DUPLICATE, VOCAB, AnkiConnect, AnkiConnectError, AnkiUnavailable,
                            ImportOptions, export_apkg, import_to_anki)
from cardmaker.anki.exporter import card_tags, sanitize_tag, stable_id
from cardmaker.pipeline.result import Card

# 导入过程允许调用的接口：只有查询和「新增」，没有任何修改或删除
SAFE_ACTIONS = {"version", "deckNames", "modelNames", "modelFieldNames", "createDeck", "createModel",
                "canAddNotesWithErrorDetail", "canAddNotes", "addNotes", "addNote"}


class FakeAnki:
    """模拟 AnkiConnect：记录每次调用，在内存里维护牌组、笔记类型和笔记。"""

    def __init__(self, existing_fronts=(), models=None, old_version=False):
        self.calls: list[str] = []
        self.decks = {"系统默认"}
        self.models = dict(models or {})  # 名字 → 字段列表
        self.fronts = set(existing_fronts)
        self.added: list[dict] = []
        self.old_version = old_version

    def __call__(self, url, payload, timeout):
        action, params = payload["action"], payload["params"]
        self.calls.append(action)
        handler = getattr(self, f"do_{action}", None)
        if handler is None or (self.old_version and action == "canAddNotesWithErrorDetail"):
            return {"result": None, "error": "unsupported action"}
        return {"result": handler(**params), "error": None}

    def do_version(self):
        return 6

    def do_deckNames(self):
        return sorted(self.decks)

    def do_modelNames(self):
        return list(self.models)

    def do_modelFieldNames(self, modelName):
        return self.models[modelName]

    def do_createDeck(self, deck):
        self.decks.add(deck)
        return 1

    def do_createModel(self, modelName, inOrderFields, css, isCloze, cardTemplates):
        self.models[modelName] = inOrderFields
        return {"name": modelName}

    def _is_dup(self, note):
        return note["fields"]["Front"] in self.fronts

    def do_canAddNotesWithErrorDetail(self, notes):
        return [{"canAdd": False, "error": "cannot create note because it is a duplicate"} if self._is_dup(n)
                else {"canAdd": True} for n in notes]

    def do_canAddNotes(self, notes):
        return [not self._is_dup(n) for n in notes]

    def do_addNotes(self, notes):
        ids = []
        for note in notes:
            self.fronts.add(note["fields"]["Front"])
            self.added.append(note)
            ids.append(len(self.added))
        return ids


def make_cards(*words, chapter=""):
    return [Card({"Front": w, "Meaning": "名：意思", "Source": "《书》 · p.1"}, {"word": w}, "1", chapter=chapter)
            for w in words]


def test_invoke_raises_on_error():
    client = AnkiConnect(transport=lambda url, payload, timeout: {"result": None, "error": "deck was not found"})
    with pytest.raises(AnkiConnectError, match="deck was not found"):
        client.invoke("findNotes", query="deck:x")


def test_unavailable_when_anki_closed():
    def closed(url, payload, timeout):
        raise AnkiUnavailable("连不上 Anki")
    assert AnkiConnect(transport=closed).is_available() is False
    assert AnkiConnect(transport=FakeAnki()).is_available() is True


def test_import_creates_note_type_and_deck(tmp_path):
    fake = FakeAnki()
    report = import_to_anki(AnkiConnect(transport=fake), make_cards("확산되다", "방치되다"), "vocab",
                            ["CardMaker::单词"], "공유 경제 이야기", ImportOptions(deck="test::CardMaker"))
    assert report.statuses == [ADDED, ADDED]
    assert fake.models["CardMaker 单词"] == VOCAB.fields
    assert "test::CardMaker" in fake.decks
    note = fake.added[0]
    assert note["deckName"] == "test::CardMaker" and note["modelName"] == "CardMaker 单词"
    assert note["fields"]["CardForward"] == "y" and note["fields"]["CardReverse"] == ""
    assert note["tags"] == ["CardMaker::单词", "CardMaker::공유_경제_이야기"]
    assert set(fake.calls) <= SAFE_ACTIONS


def test_duplicates_are_marked_and_skipped():
    fake = FakeAnki(existing_fronts={"확산되다"})
    report = import_to_anki(AnkiConnect(transport=fake), make_cards("확산되다", "방치되다"), "vocab", [], "书",
                            ImportOptions(deck="test::CardMaker"))
    assert report.statuses == [DUPLICATE, ADDED]
    assert [n["fields"]["Front"] for n in fake.added] == ["방치되다"]
    assert "1 张 Anki 里已经有了" in report.summary()


def test_existing_note_type_with_other_fields_is_left_untouched():
    fake = FakeAnki(models={"CardMaker 单词": ["Front", "Back"]})  # 用户手动改过的同名笔记类型
    report = import_to_anki(AnkiConnect(transport=fake), make_cards("합의"), "vocab", [], "书",
                            ImportOptions(deck="test::CardMaker"))
    assert report.model_name == "CardMaker 单词 2"
    assert fake.models["CardMaker 单词"] == ["Front", "Back"]  # 原来的没被改
    assert set(fake.calls) <= SAFE_ACTIONS


def test_matching_note_type_is_reused():
    fake = FakeAnki(models={"CardMaker 单词": list(VOCAB.fields)})
    import_to_anki(AnkiConnect(transport=fake), make_cards("합의"), "vocab", [], "书", ImportOptions(deck="d"))
    assert "createModel" not in fake.calls


def test_old_ankiconnect_without_error_detail():
    fake = FakeAnki(existing_fronts={"합의"}, old_version=True)
    report = import_to_anki(AnkiConnect(transport=fake), make_cards("합의", "갈등"), "vocab", [], "书",
                            ImportOptions(deck="d"))
    assert report.statuses == [DUPLICATE, ADDED]


def test_direction_options():
    fake = FakeAnki()
    import_to_anki(AnkiConnect(transport=fake), make_cards("합의"), "vocab", [], "书",
                   ImportOptions(deck="d", forward=False, reverse=True))
    assert fake.added[0]["fields"]["CardForward"] == "" and fake.added[0]["fields"]["CardReverse"] == "y"
    with pytest.raises(ValueError):
        import_to_anki(AnkiConnect(transport=fake), make_cards("x"), "vocab", [], "书",
                       ImportOptions(deck="d", forward=False, reverse=False))


def test_custom_model_name_for_testing():
    fake = FakeAnki()
    report = import_to_anki(AnkiConnect(transport=fake), make_cards("합의"), "vocab", [], "书",
                            ImportOptions(deck="test::CardMaker", model_name="test-CardMaker 单词"))
    assert report.model_name == "test-CardMaker 单词" and "CardMaker 单词" not in fake.models


def test_tags():
    assert sanitize_tag(" 공유 경제  이야기 ") == "공유_경제_이야기"
    card = make_cards("x", chapter="1장 시작")[0]
    assert card_tags(card, ["CardMaker::单词"], "책 이름", ["自学 单词", ""]) == [
        "CardMaker::单词", "CardMaker::책_이름::1장_시작", "自学_单词"]


def _apkg_counts(path):
    with zipfile.ZipFile(path) as z, open(path.with_suffix(".anki2"), "wb") as out:
        out.write(z.read("collection.anki2"))
    with sqlite3.connect(path.with_suffix(".anki2")) as db:
        return db.execute("select count(*) from notes").fetchone()[0], db.execute("select count(*) from cards").fetchone()[0]


@pytest.mark.parametrize("forward,reverse,cards_per_note", [(True, False, 1), (False, True, 1), (True, True, 2)])
def test_apkg_export(tmp_path, forward, reverse, cards_per_note):
    path = export_apkg(make_cards("확산되다", "방치되다"), "vocab", ["CardMaker::单词"], "书",
                       ImportOptions(deck="CardMaker::书", forward=forward, reverse=reverse), tmp_path / "out.apkg")
    notes, cards = _apkg_counts(path)
    assert notes == 2 and cards == 2 * cards_per_note


def test_stable_ids():
    assert stable_id("model:CardMaker 单词") == stable_id("model:CardMaker 单词")
    assert (1 << 30) <= stable_id("x") < (1 << 31)
