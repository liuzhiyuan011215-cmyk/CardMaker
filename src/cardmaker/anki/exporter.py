"""把卡片写入 Anki：优先通过 AnkiConnect 直接写入；Anki 没打开时导出 .apkg 文件（双击即可导入）。

原则：只新增，绝不修改或删除用户 Anki 里已有的任何东西。
- 笔记类型不存在就新建；同名的已存在但字段不同（比如被手动改过），就另建一个带编号的，不去改它；
- Anki 里已经有的卡片（第一个字段相同）标记为「已存在」并跳过，不影响其他卡片。
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..pipeline.result import Card
from .connect import AnkiConnect
from .notetypes import NOTE_TYPES, NoteType

log = logging.getLogger(__name__)

ADDED, DUPLICATE, FAILED = "已导入", "已存在", "失败"


@dataclass
class ImportOptions:
    deck: str
    forward: bool = True  # 生成「外语→释义」卡
    reverse: bool = False  # 生成「释义→外语」卡
    tags: list[str] = field(default_factory=list)  # 额外标签
    model_name: str | None = None  # 笔记类型名，不填用默认（测试时用带 test 的名字）


@dataclass
class ImportReport:
    deck: str
    model_name: str
    statuses: list[str]  # 与输入的卡片一一对应：已导入 / 已存在 / 失败
    errors: list[str] = field(default_factory=list)

    def count(self, status: str) -> int:
        return self.statuses.count(status)

    def summary(self) -> str:
        text = f"已导入 {self.count(ADDED)} 张到「{self.deck}」"
        if self.count(DUPLICATE):
            text += f"，{self.count(DUPLICATE)} 张 Anki 里已经有了，已跳过"
        if self.count(FAILED):
            text += f"，{self.count(FAILED)} 张失败"
        return text


def sanitize_tag(text: str) -> str:
    """Anki 标签里不能有空格，用下划线代替。"""
    return re.sub(r"\s+", "_", text.strip()).replace('"', "")


def card_tags(card: Card, template_tags: list[str], book_title: str, extra: list[str] = ()) -> list[str]:
    """标签：领域（来自模板）+ 书名::章节（有章节时）+ 用户额外填的。页码写在 Source 字段里，不单独打标签。"""
    source = f"CardMaker::{sanitize_tag(book_title)}"
    if card.chapter:
        source += f"::{sanitize_tag(card.chapter)}"
    tags = [*template_tags, source, *(sanitize_tag(t) for t in extra if t.strip())]
    return list(dict.fromkeys(tags))


def resolve_note_type(key: str, options: ImportOptions) -> NoteType:
    note_type = NOTE_TYPES[key]
    return note_type.renamed(options.model_name) if options.model_name else note_type


def note_fields(card: Card, note_type: NoteType, options: ImportOptions) -> dict[str, str]:
    fields = {name: card.fields.get(name, "") for name in note_type.fields}
    if "CardForward" in fields:  # 单词卡：按用户选的方向生成正向 / 反向卡
        fields["CardForward"] = "y" if options.forward else ""
        fields["CardReverse"] = "y" if options.reverse else ""
    return fields


def has_directions(note_type_key: str) -> bool:
    return "CardForward" in NOTE_TYPES[note_type_key].fields


def ensure_note_type(client: AnkiConnect, note_type: NoteType) -> str:
    """返回可用的笔记类型名：已存在且字段一致就直接用，否则新建（必要时加编号），从不修改已有的。"""
    existing = set(client.model_names())
    name, n = note_type.name, 2
    while name in existing:
        if client.model_field_names(name) == note_type.fields:
            return name
        name, n = f"{note_type.name} {n}", n + 1
    client.create_model(name, note_type.fields, note_type.anki_templates(), note_type.css, note_type.is_cloze)
    log.info("已创建笔记类型：%s", name)
    return name


def import_to_anki(client: AnkiConnect, cards: list[Card], note_type_key: str, template_tags: list[str],
                   book_title: str, options: ImportOptions, media: list[Path] = ()) -> ImportReport:
    if has_directions(note_type_key) and not (options.forward or options.reverse):
        raise ValueError("至少要选一个方向")
    note_type = resolve_note_type(note_type_key, options)
    model = ensure_note_type(client, note_type)
    client.create_deck(options.deck)  # 已存在时什么都不做
    for path in media:  # 发音文件
        client.store_media_file(path)

    notes = [{"deckName": options.deck, "modelName": model, "fields": note_fields(c, note_type, options),
              "tags": card_tags(c, template_tags, book_title, options.tags),
              "options": {"allowDuplicate": False}} for c in cards]
    report = ImportReport(options.deck, model, [FAILED] * len(cards))
    checks = client.can_add_notes(notes) if notes else []
    addable = []
    for i, check in enumerate(checks):
        if check.get("canAdd"):
            addable.append(i)
        elif "duplicate" in str(check.get("error", "")):
            report.statuses[i] = DUPLICATE
        else:
            report.errors.append(f"{cards[i].fields.get('Front', '')}：{check.get('error')}")
    if addable:
        for i, note_id in zip(addable, client.add_notes([notes[i] for i in addable])):
            if note_id:
                report.statuses[i] = ADDED
            else:
                report.errors.append(f"{cards[i].fields.get('Front', '')}：添加失败")
    log.info("导入 Anki：%s", report.summary())
    return report


# ---------------------------------------------------------------------- .apkg 导出
def stable_id(text: str) -> int:
    """genanki 需要固定的数字 id：同一个笔记类型 / 牌组每次导出都用同一个 id，重复导入时不会多出副本。"""
    return (1 << 30) + int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16) % (1 << 30)


def export_apkg(cards: list[Card], note_type_key: str, template_tags: list[str], book_title: str,
                options: ImportOptions, path: Path, media: list[Path] = ()) -> Path:
    import genanki

    if has_directions(note_type_key) and not (options.forward or options.reverse):
        raise ValueError("至少要选一个方向")
    note_type = resolve_note_type(note_type_key, options)
    model = genanki.Model(stable_id(f"model:{note_type.name}"), note_type.name,
                          fields=[{"name": f} for f in note_type.fields],
                          templates=[{"name": t.name, "qfmt": t.front, "afmt": t.back} for t in note_type.templates],
                          css=note_type.css,
                          model_type=genanki.Model.CLOZE if note_type.is_cloze else genanki.Model.FRONT_BACK)
    deck = genanki.Deck(stable_id(f"deck:{options.deck}"), options.deck)
    for card in cards:
        fields = note_fields(card, note_type, options)
        deck.add_note(genanki.Note(
            model=model, fields=[fields[f] for f in note_type.fields],
            tags=card_tags(card, template_tags, book_title, options.tags),
            guid=genanki.guid_for(note_type.name, fields[note_type.fields[0]]),  # 重复导入时是更新而不是重复
        ))
    path.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck, media_files=[str(p) for p in media]).write_to_file(str(path))
    return path
