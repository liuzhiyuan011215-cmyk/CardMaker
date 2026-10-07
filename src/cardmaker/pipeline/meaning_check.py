"""用词典核对单词卡的释义：防幻觉的第二道检查。

第一道检查（postprocess.check_grounding）只能证明单词和原句在原文里找得到；释义是大模型写的，
原文里没有，相似度比对查不了，所以拿词典来核对（韩语单词查韩国语基础词典，英语、中文单词查维基词典）：
1. 查词典。词典里没有这个词 → 标「词典未收录」（只提醒，不删）。
2. 这个词只有一个义项，而且释义和词典的译词对得上 → 「一致」，不再多问。
3. 多义词，或者释义对不上 → 把原句和词典义项（编号 + 释义 + 译词）交给大模型，让它只在这些编号里选，
   不许自己写（这一步看不到卡片上的释义，免得被它带偏）：
   - 选中的义项就是释义对上的那个 → 「一致」；
   - 选中了别的义项 → 释义换成词典里那个义项的译词，标「已按词典更正」，原来的释义留着备查；
   - 选中的义项没有这种语言的译词（例如维基词典只有英语释义）→ 再请模型对照这个义项判断释义对不对，
     不对就把词典义项译过来更正；
   - 一个都不符合 → 标「词典无此义」并取消勾选，用户确认后才会导入。
结果记在 card.context["dict_check"] 里（随卡片一起存进数据库），界面和评估都从这里读。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable

from ..dictionary import DictError, Entry, Sense, lookup_key
from ..llm.client import ChatModel, Usage, parse_json_object
from ..progress import CHECK, ProgressEvent
from .result import Card

log = logging.getLogger(__name__)

VERIFIED, CORRECTED, UNMATCHED, NOT_FOUND, ERROR = "verified", "corrected", "unmatched", "not_found", "error"
LABELS = {VERIFIED: "一致", CORRECTED: "已按词典更正", UNMATCHED: "词典无此义", NOT_FOUND: "词典未收录",
          ERROR: "未核对"}
MAX_SENSES = 15  # 义项特别多的词（例如「하다」、bank）只给这么多个，免得提示词太长
BATCH = 12  # 每次请模型选义项的单词数

_BRACKETS = re.compile(r"[（(【\[].*?[）)】\]]")
_SPLIT = re.compile(r"[，,；;、/／·\s]+")
_STRIP = "。.…～~-—「」“”\"'：:"


def glosses(text: str) -> list[str]:
    """释义 → 一个个译词：去掉括号里的补充说明，按逗号、分号、斜杠拆开，统一全角半角和大小写。"""
    text = unicodedata.normalize("NFKC", _BRACKETS.sub(" ", text or "")).casefold()
    return [p for p in (part.strip(_STRIP) for part in _SPLIT.split(text)) if p]


def _same_gloss(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if len(short) >= 2:
        return short in long
    # 单字只和很短的词比：「梨」≈「梨子」，「船」≈「船只」
    return len(long) <= 3 and short in long


def pick_senses(entries: list[Entry]) -> list[tuple[Entry, Sense]]:
    """义项太多时各词条轮流取（每个词条的第 1 个义项优先），后面的同形词（例如 bank「河岸」）才不会被挤掉。"""
    ranked = [(rank, i, entry, sense) for i, entry in enumerate(entries) for rank, sense in enumerate(entry.senses)]
    kept = sorted(sorted(ranked, key=lambda t: t[0])[:MAX_SENSES], key=lambda t: (t[1], t[0]))  # 再按词典顺序排回去
    return [(entry, sense) for _, _, entry, sense in kept]


def matches(meaning: str, sense: Sense) -> bool:
    """卡片上的释义和词典的这个义项对不对得上：和译词一致，或者出现在译文释义里。"""
    mine = glosses(meaning)
    if any(_same_gloss(a, b) for a in mine for b in glosses(sense.trans_word)):
        return True
    dfn = unicodedata.normalize("NFKC", sense.trans_dfn).casefold()
    return any(len(a) >= 2 and a in dfn for a in mine)


# ---------------------------------------------------------------------- 让模型在义项里选
CHOOSER_SYSTEM = ("你是词典义项判断助手。根据原句，判断每个单词在句中用的是词典里的哪个义项。"
                  "只能从给出的编号里选一个；都不符合就填 0。不要解释，只输出一个 json 对象。")


def choose_senses(llm: ChatModel, jobs: list[dict]) -> dict[int, int]:
    """jobs: [{"id", "word", "sentence", "senses": [{"n", "ko", "zh"}]}] → {id: 选中的编号（0 = 都不符合）}。"""
    user = ('为下面每个单词选择义项编号，输出格式：{"choices": [{"id": 1, "sense": 2}, ...]}\n'
            + json.dumps(jobs, ensure_ascii=False))
    content, _ = llm.chat([{"role": "system", "content": CHOOSER_SYSTEM}, {"role": "user", "content": user}])
    valid = {job["id"]: {s["n"] for s in job["senses"]} | {0} for job in jobs}
    choices = {}
    for c in parse_json_object(content).get("choices") or []:
        try:
            job_id, sense = int(c["id"]), int(c["sense"])
        except (KeyError, TypeError, ValueError):
            continue
        if sense in valid.get(job_id, ()):
            choices[job_id] = sense
    return choices


JUDGE_SYSTEM = ("你是词典释义核对助手。词典义项是权威的。对每个单词，判断卡片上的释义 meaning 是不是这个义项的意思"
                "（意思相同的不同说法也算对）。对就填 same: true；不对就填 same: false，并把词典义项译成{language}"
                "写进 fix，要像词典译词一样简短。不要解释，只输出一个 json 对象。")


def judge_meanings(llm: ChatModel, jobs: list[dict], meaning_language: str) -> dict[int, dict]:
    """jobs: [{"id", "word", "sentence", "sense", "meaning"}] → {id: {"same": bool, "fix": 译过来的释义}}。"""
    user = ('逐个核对，输出格式：{"results": [{"id": 1, "same": true}, {"id": 2, "same": false, "fix": "…"}]}\n'
            + json.dumps(jobs, ensure_ascii=False))
    content, _ = llm.chat([{"role": "system", "content": JUDGE_SYSTEM.format(language=meaning_language)},
                           {"role": "user", "content": user}])
    ids, verdicts = {job["id"] for job in jobs}, {}
    for r in parse_json_object(content).get("results") or []:
        try:
            job_id, same = int(r["id"]), r["same"]
        except (KeyError, TypeError, ValueError):
            continue
        if job_id in ids and isinstance(same, bool):
            verdicts[job_id] = {"same": same, "fix": str(r.get("fix") or "").strip()}
    return verdicts


class _Metered:
    """转发给模型，顺便记下每次调用用掉的 token。"""

    def __init__(self, llm: ChatModel, usage: Usage):
        self.llm, self.usage = llm, usage

    def chat(self, messages: list[dict]) -> tuple[str, Usage]:
        content, used = self.llm.chat(messages)
        self.usage.add(used)
        return content, used


# ---------------------------------------------------------------------- 主流程
def check_meanings(cards: list[Card], template, dictionary, llm: ChatModel | None, meaning_language: str = "中文",
                   progress: Callable[[ProgressEvent], None] | None = None,
                   cancel: threading.Event | None = None, usage: Usage | None = None) -> Counter:
    """逐张核对单词卡释义，就地修改卡片，返回各结果的数量。模板没声明 dictionary 时什么都不做。

    usage：给了就把核对时调用模型用掉的 token 加进去（算进任务的用量和费用）。
    """
    spec = template.spec.output.dictionary
    if spec is None or dictionary is None:
        return Counter()
    if getattr(llm, "local", False):  # 本地小模型选义项常选错，只做字面比对，对不上的标出来让人确认
        llm = None
    elif llm is not None and usage is not None:
        llm = _Metered(llm, usage)
    say = progress or (lambda event: None)
    todo = [c for c in cards if dictionary.covers(str(c.data.get(spec.word) or ""), meaning_language)
            and not (spec.kind and c.data.get(spec.kind) == "phrase")]  # 短语、句型词典里大多没有，不核对
    stats: Counter = Counter()
    pending: list[tuple[Card, list[tuple[Entry, Sense]], list[int]]] = []

    for n, card in enumerate(todo, 1):
        if cancel is not None and cancel.is_set():
            break
        word, meaning = str(card.data.get(spec.word)), str(card.data.get(spec.meaning) or "")
        try:
            entries = dictionary.lookup(word, meaning_language)
        except DictError as exc:  # Key 不对、没网、额度用完：剩下的都标「未核对」，不影响出卡
            log.warning("词典核对中止：%s", exc)
            for rest in todo[n - 1:]:
                _mark(rest, ERROR, note=str(exc))
                stats[ERROR] += 1
            say(ProgressEvent(CHECK, n - 1, len(todo), f"词典核对中止：{exc}"))
            break
        senses = pick_senses(entries)
        if not senses:
            _mark(card, NOT_FOUND, word=lookup_key(word))
            stats[NOT_FOUND] += 1
            continue
        hits = [i for i, (_, s) in enumerate(senses) if matches(meaning, s)]
        if len(senses) == 1 and hits:
            _mark(card, VERIFIED, sense=_describe(*senses[0]), link=senses[0][0].link, source=senses[0][0].source)
            stats[VERIFIED] += 1
        else:
            pending.append((card, senses, hits))
        if n % 10 == 0 or n == len(todo):
            say(ProgressEvent(CHECK, n, len(todo), f"正在用词典核对释义：{n}/{len(todo)}"))

    for start in range(0, len(pending), BATCH):
        if cancel is not None and cancel.is_set():
            break
        batch = pending[start:start + BATCH]
        jobs = [{"id": k, "word": str(card.data.get(spec.word)),
                 "sentence": str(card.data.get(spec.sentence) or "") if spec.sentence else "",
                 "senses": [{"n": i + 1, "definition": s.definition, "translation": s.trans_word}
                            for i, (_, s) in enumerate(senses)]}
                for k, (card, senses, _) in enumerate(batch)]
        try:
            choices = choose_senses(llm, jobs) if llm is not None else {}
        except Exception as exc:  # 选义项失败不影响出卡：退回只看字面是否对得上
            log.warning("请模型选择义项失败：%s", exc)
            choices = {}
        # 选中的义项没有这种语言的译词，字面比不了：请模型对照这个义项判断
        unclear = [{**{key: job[key] for key in ("id", "word", "sentence")},
                    "sense": senses[choices[k] - 1][1].definition, "meaning": str(card.data.get(spec.meaning) or "")}
                   for k, (job, (card, senses, hits)) in enumerate(zip(jobs, batch))
                   if choices.get(k) and choices[k] - 1 not in hits and not _translation(senses[choices[k] - 1][1])]
        verdicts = {}
        if unclear:
            try:
                verdicts = judge_meanings(llm, unclear, meaning_language)
            except Exception as exc:
                log.warning("请模型对照义项失败：%s", exc)
        for k, (card, senses, hits) in enumerate(batch):
            stats[_apply_choice(card, template, spec, senses, hits, choices.get(k), verdicts.get(k))] += 1
        done = min(start + BATCH, len(pending))
        say(ProgressEvent(CHECK, done, len(pending), f"正在核对多义词的义项：{done}/{len(pending)}"))
    return stats


def _translation(sense: Sense) -> str:
    return sense.trans_word or sense.trans_dfn


def _apply_choice(card: Card, template, spec, senses: list[tuple[Entry, Sense]], hits: list[int],
                  choice: int | None, verdict: dict | None = None) -> str:
    meaning = str(card.data.get(spec.meaning) or "")
    if choice is None:  # 模型没给出选择：只能看字面
        if hits:
            return _mark(card, VERIFIED, sense=_describe(*senses[hits[0]]), note="未经语境确认")
        if not any(_translation(s) for _, s in senses):  # 词典只有外语释义，字面没法比
            return _mark(card, ERROR, note="词典只有英语释义，需要大模型对照，这次没有核对")
        return _mark(card, UNMATCHED, note="释义和词典的义项都对不上")
    if choice == 0:
        card.selected = False
        return _mark(card, UNMATCHED, note="词典里没有符合原句的义项，已取消勾选，请核对", deselected=True)
    entry, sense = senses[choice - 1]
    found = {"sense": _describe(entry, sense), "link": entry.link, "source": entry.source}
    if choice - 1 in hits:
        return _mark(card, VERIFIED, **found)
    replacement, note = _translation(sense), ""
    if not replacement:  # 这个义项没有这种语言的译词：看模型对照词典义项的判断
        if verdict and verdict["same"]:
            return _mark(card, VERIFIED, **found, note="由 AI 对照词典义项确认")
        if not (verdict and verdict["fix"]) or glosses(verdict["fix"]) == glosses(meaning):  # 说不对又给回原释义
            return _mark(card, UNMATCHED, **found, note="语境对应的义项没有译文，请核对")
        replacement, note = verdict["fix"], "由 AI 按词典义项译出"
    card.data[spec.meaning] = replacement
    card.fields = template.render_card_fields(card.data, card.context)
    return _mark(card, CORRECTED, **found, original=meaning, note=note)


def _describe(entry: Entry, sense: Sense) -> str:
    trans = f" {sense.trans_word}" if sense.trans_word else ""
    return f"{entry.label} ({entry.pos}) {sense.order}.{trans}：{sense.definition}"


def _mark(card: Card, status: str, **info) -> str:
    card.context["dict_check"] = {"status": status, **{k: v for k, v in info.items() if v not in ("", None)}}
    return status


def check_status(card: Card) -> str:
    return (card.context.get("dict_check") or {}).get("status", "")


def summarize(cards: list[Card]) -> Counter:
    return Counter(s for s in (check_status(c) for c in cards) if s)
