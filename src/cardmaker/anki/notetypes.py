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
import functools
from dataclasses import dataclass

from ..i18n import tr

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
    is_cloze: bool = False  # Anki 原生填空类型：一条笔记里有几处 {{c1::…}}、{{c2::…}} 就生成几张卡

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

# ---------------------------------------------------------------------- 填空卡
_COMMON_CSS = """.card {
  font-family: -apple-system, "PingFang SC", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  font-size: 20px; line-height: 1.7; text-align: left; color: #333333; background-color: #ffffff;
  max-width: 720px; margin: 0 auto; padding: 8px 16px;
}
.section { margin: 16px 0; }
.label { font-size: 14px; color: #888888; margin-bottom: 4px; }
.quote { font-size: 17px; color: #555555; border-left: 3px solid #d0d7e2; padding-left: 10px; }
.source { margin-top: 24px; font-size: 13px; color: #aaaaaa; }
pre { background: #f6f8fa; border-radius: 6px; padding: 10px 12px; font-size: 15px; line-height: 1.5;
      white-space: pre-wrap; text-align: left; }
.nightMode .card, .night_mode .card { background-color: #1a1b20; color: #e2e2e2; }
.nightMode .quote, .night_mode .quote { color: #bbbbbb; border-color: #44474f; }
.nightMode pre, .night_mode pre { background: #2a2b31; }
"""

CLOZE_FRONT = """<div class="cloze-text">{{cloze:Text}}</div>"""
CLOZE_BACK = """<div class="cloze-text">{{cloze:Text}}</div>
{{#Extra}}<div class="section"><div class="label">💡 要点</div>{{Extra}}</div>{{/Extra}}
{{#SourceText}}<div class="section"><div class="label">📖 原文</div><div class="quote">{{SourceText}}</div></div>{{/SourceText}}
{{#Source}}<div class="source">{{Source}}</div>{{/Source}}"""
CLOZE_CSS = _COMMON_CSS + """.cloze { font-weight: 700; color: #2563eb; }
.nightMode .cloze, .night_mode .cloze { color: #6aa9ff; }
"""

CLOZE = NoteType(
    name="CardMaker 填空",
    fields=["Text", "Extra", "SourceText", "Source"],
    templates=[CardTemplate("填空", CLOZE_FRONT, CLOZE_BACK)],
    css=CLOZE_CSS,
    is_cloze=True,
)

# ---------------------------------------------------------------------- 选择题
_OPTIONS = """<div class="options {cls}">
  <div class="option" id="{prefix}A"><span class="letter">A</span>{{{{A}}}}</div>
  <div class="option" id="{prefix}B"><span class="letter">B</span>{{{{B}}}}</div>
  <div class="option" id="{prefix}C"><span class="letter">C</span>{{{{C}}}}</div>
  <div class="option" id="{prefix}D"><span class="letter">D</span>{{{{D}}}}</div>
</div>"""

MCQ_FRONT = """<div class="question">{{Question}}</div>
""" + _OPTIONS.format(cls="", prefix="q-")

MCQ_BACK = """<div class="question">{{Question}}</div>
""" + _OPTIONS.format(cls="answered", prefix="a-") + """
<div class="answer-line">正确答案：<b>{{Answer}}</b></div>
{{#Explanation}}<div class="section"><div class="label">💡 解析</div>{{Explanation}}</div>{{/Explanation}}
{{#SourceText}}<div class="section"><div class="label">📖 原文</div><div class="quote">{{SourceText}}</div></div>{{/SourceText}}
{{#Source}}<div class="source">{{Source}}</div>{{/Source}}
<script>
  // 把正确选项标成绿色（电脑版、AnkiMobile、AnkiDroid 都支持）
  (function () {
    var answer = "{{Answer}}".replace(/<[^>]*>/g, "").trim().toUpperCase();
    var el = document.getElementById("a-" + answer);
    if (el) el.classList.add("correct");
  })();
</script>"""

MCQ_CSS = _COMMON_CSS + """.question { font-size: 21px; font-weight: 600; margin: 8px 0 16px; }
.option { border: 1px solid #e0e0e0; border-radius: 10px; padding: 10px 14px; margin: 8px 0; }
.letter { display: inline-block; width: 26px; font-weight: 700; color: #2563eb; }
.option.correct { border-color: #16a34a; background: #ecfdf3; }
.answer-line { margin-top: 14px; font-size: 17px; }
.nightMode .option, .night_mode .option { border-color: #3a3b41; }
.nightMode .option.correct, .night_mode .option.correct { background: #173d2a; border-color: #22c55e; }
"""

MCQ = NoteType(
    name="CardMaker 选择题",
    fields=["Question", "A", "B", "C", "D", "Answer", "Explanation", "SourceText", "Source"],
    templates=[CardTemplate("选择题", MCQ_FRONT, MCQ_BACK)],
    css=MCQ_CSS,
)

NOTE_TYPES = {"vocab": VOCAB, "cloze": CLOZE, "mcq": MCQ}  # 模板 YAML 里的 note_type → 笔记类型定义
CARD_LABELS = ["📖 原文", "写出对应的单词", "💡 要点", "💡 解析", "正确答案："]  # 卡片模板里写死的中文标签


def localized(note_type: NoteType) -> NoteType:
    """按界面语言换掉卡片上的标签和笔记类型名。韩语的名字不同，Anki 里原来的中文笔记类型不受影响。"""
    swap = lambda html: functools.reduce(lambda text, label: text.replace(label, tr(label)), CARD_LABELS, html)
    return dataclasses.replace(note_type, name=tr(note_type.name), templates=[
        CardTemplate(tr(t.name), swap(t.front), swap(t.back)) for t in note_type.templates])
