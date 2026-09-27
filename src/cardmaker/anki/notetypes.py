"""程序自动创建的 Anki 笔记类型（字段 + 卡片模板 + 样式）。

单词笔记类型一条笔记最多出两张卡：
- 「外语→释义」：看到单词，口头说出意思
- 「释义→外语」：看到意思，动笔写出单词
由 CardForward / CardReverse 两个字段控制：字段非空才生成对应的卡（Anki 的标准做法，
正面为空的卡不会生成），所以导入时可以选只要一个方向，或者两个都要。
样式参照用户现有的「问答题」模板，支持夜间模式。
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

VOCAB_FIELDS = ["Front", "Meaning", "SourceSentence", "SourceTranslation", "Example", "ExampleTranslation",
                "ExampleLabel", "Audio", "ExampleAudio", "Source", "CardForward", "CardReverse"]

_SECTIONS = """
{{#SourceSentence}}
<div class="section">
  <div class="label">📖 原文</div>
  <div class="sentence">{{SourceSentence}}</div>
  {{#SourceTranslation}}<div class="translation">{{SourceTranslation}}</div>{{/SourceTranslation}}
</div>
{{/SourceSentence}}
{{#Example}}
<div class="section">
  <div class="label">{{ExampleLabel}}</div>
  <div class="sentence">{{Example}} {{ExampleAudio}}</div>
  {{#ExampleTranslation}}<div class="translation">{{ExampleTranslation}}</div>{{/ExampleTranslation}}
</div>
{{/Example}}
{{#Source}}<div class="source">{{Source}}</div>{{/Source}}
"""

FORWARD_FRONT = """{{#CardForward}}
<div class="word">{{Front}}</div>
{{Audio}}
{{/CardForward}}"""

FORWARD_BACK = """{{FrontSide}}
<hr id="answer">
<div class="meaning-box">{{Meaning}}</div>
""" + _SECTIONS

REVERSE_FRONT = """{{#CardReverse}}
<div class="hint">写出对应的单词</div>
<div class="meaning-front">{{Meaning}}</div>
{{/CardReverse}}"""

REVERSE_BACK = """{{FrontSide}}
<hr id="answer">
<div class="word">{{Front}}</div>
{{Audio}}
""" + _SECTIONS

VOCAB_CSS = """.card {
  font-family: -apple-system, "PingFang SC", "Apple SD Gothic Neo", "Malgun Gothic", "Nanum Gothic", sans-serif;
  font-size: 20px; line-height: 1.6; text-align: center; color: #333333; background-color: #ffffff;
}
.word { font-size: 42px; font-weight: 700; margin: 24px 0 12px; }
.meaning-front { font-size: 28px; margin: 12px 0; }
.hint { font-size: 14px; color: #999999; margin-top: 16px; }
.meaning-box {
  max-width: 640px; margin: 16px auto; padding: 14px 16px; font-size: 20px;
  background: #f4f4f4; border: 1px solid #e0e0e0; border-radius: 8px;
}
.section { max-width: 640px; margin: 18px auto; text-align: left; }
.label { font-size: 14px; color: #888888; margin-bottom: 4px; }
.sentence { font-size: 20px; }
.sentence b { color: #2563eb; }
.translation { font-size: 16px; color: #777777; }
.source { margin-top: 24px; font-size: 13px; color: #aaaaaa; }

/* 夜间模式 */
.nightMode .card, .night_mode .card { background-color: #1a1b20; color: #e2e2e2; }
.nightMode .meaning-box, .night_mode .meaning-box { background: #2a2b31; border-color: #3a3b41; }
.nightMode .sentence b, .night_mode .sentence b { color: #6aa9ff; }
.nightMode .translation, .night_mode .translation, .nightMode .label, .night_mode .label { color: #a0a0a0; }
"""


@dataclass(frozen=True)
class CardTemplate:
    name: str
    front: str
    back: str


@dataclass(frozen=True)
class NoteType:
    name: str
    fields: list[str]
    templates: list[CardTemplate]
    css: str

    def renamed(self, name: str) -> "NoteType":
        return dataclasses.replace(self, name=name)

    def anki_templates(self) -> list[dict]:
        """AnkiConnect createModel 需要的格式。"""
        return [{"Name": t.name, "Front": t.front, "Back": t.back} for t in self.templates]


VOCAB = NoteType(
    name="CardMaker 单词",
    fields=VOCAB_FIELDS,
    templates=[CardTemplate("外语→释义", FORWARD_FRONT, FORWARD_BACK),
               CardTemplate("释义→外语", REVERSE_FRONT, REVERSE_BACK)],
    css=VOCAB_CSS,
)

NOTE_TYPES = {"vocab": VOCAB}  # 模板 YAML 里的 note_type → 笔记类型定义
