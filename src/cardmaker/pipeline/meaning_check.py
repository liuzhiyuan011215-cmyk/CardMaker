"""用词典核对单词卡的释义：防幻觉的第二道检查。

第一道检查（postprocess.check_grounding）只能证明单词和原句在原文里找得到；释义是大模型写的，
原文里没有，相似度比对查不了，所以拿词典来核对（韩语单词查韩国语基础词典，英语、中文单词查维基词典）：
规则：没有得到词典确认的卡一律取消勾选，用户看过才能进 Anki，错的卡不能悄悄混进去。
1. 查词典。词典里没有这个词（例如 OCR 把 book 认成 buuk）→ 标「词典未收录」并取消勾选；
   短语、句型词典里常常没有，查不到时标「未经词典核对」并取消勾选。
2. 这个词只有一个义项，而且释义和词典的译词对得上 → 「一致」，不再多问。
3. 多义词，或者释义对不上 → 把原句和词典义项（编号 + 释义 + 译词）交给大模型，让它只在这些编号里选，
   不许自己写（这一步看不到卡片上的释义，免得被它带偏）：
   - 选中的义项就是释义对上的那个 → 「一致」；
   - 选中了别的义项 → 释义换成词典里那个义项的译词，标「已按词典更正」，原来的释义留着备查；
   - 选中的义项没有这种语言的译词（例如维基词典只有英语释义）→ 再请模型对照这个义项判断释义对不对，
     不对就把词典义项译过来更正；译文要能反查词典对上，否则取消勾选；
   - 一个都不符合 → 标「词典无此义」并取消勾选，用户确认后才会导入。
结果记在 card.context["dict_check"] 里（随卡片一起存进数据库），界面和评估都从这里读。

释义定下来之后才配例句（settle_examples）：书上有例句就用书上的，否则用词典这个义项的例句，
词典也没有才让模型按这个释义造一句。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import replace

from ..dictionary import DIRECT_SOURCES,DictError, Entry, Sense, lookup_key
from ..llm.client import ChatModel, Usage, parse_json_object
from ..progress import CHECK, ProgressEvent
from ..tts import detect_language
from ..templates.loader import find_term
from .result import Card

log = logging.getLogger(__name__)

VERIFIED, CORRECTED, UNMATCHED, NOT_FOUND, ERROR = "verified", "corrected", "unmatched", "not_found", "error"
OCR_SUSPECT, UNCHECKED = "ocr_suspect", "unchecked"
LABELS = {VERIFIED: "一致", CORRECTED: "已按词典更正", UNMATCHED: "词典无此义", NOT_FOUND: "词典未收录",
          ERROR: "未核对", OCR_SUSPECT: "疑似 OCR 错字", UNCHECKED: "未经词典核对"}
LANGUAGE_CODES = {"中文": "zh", "韩语": "ko", "英语": "en", "日语": "ja"}
HELD = {OCR_SUSPECT, NOT_FOUND, UNMATCHED, UNCHECKED, ERROR}
MAX_SENSES = 15  # 义项特别多的词（例如「하다」、bank）只给这么多个，免得提示词太长
BATCH = 12  # 每次请模型选义项的单词数

_BRACKETS = re.compile(r"[（(【\[].*?[）)】\]]")
_SPLIT = re.compile(r"[，,；;、/／·]+")
_LATIN = re.compile(r"[a-z]{2}")
_CJK = re.compile(r"[\u3131-\u318e\u4e00-\u9fff\uac00-\ud7a3]")
_ENGLISH_LEAD = re.compile(r"^(?:to|a|an|the|be)\s+")
_STRIP = "。.…～~-—「」“”\"'：:"


def glosses(text: str) -> list[str]:
    """释义 → 一个个译词：去掉括号里的补充说明，按逗号、分号、斜杠拆开，统一全角半角和大小写。"""
    text = unicodedata.normalize("NFKC", _BRACKETS.sub(" ", text or "")).casefold()
    out = []
    for part in _SPLIT.split(text):
        english = _LATIN.search(part) and not _CJK.search(part)
        for p in ([_ENGLISH_LEAD.sub("",part.strip())] if english else part.split()):
            if p.strip(_STRIP):
                out.append(p.strip(_STRIP))
    return out


def _same_gloss(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if _LATIN.search(short):
        return len(short) >= 3 and re.search(rf"\b{re.escape(short)}\b",long) is not None 
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
    """卡片上的释义和词典的这个义项对不对得上：要和译词一致。
    不看译文解释：닫다 的解释里有「打开」二字（把打开的东西关上），会让反义词也算对上。"""
    return any(_same_gloss(a, b) for a in glosses(meaning) for b in glosses(sense.trans_word))


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
    todo = [c for c in cards if dictionary.covers(str(c.data.get(spec.word) or ""), meaning_language)]
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
                _hold(rest, ERROR, note=str(exc))
                stats[ERROR] += 1
            say(ProgressEvent(CHECK, n - 1, len(todo), f"词典核对中止：{exc}"))
            break
        senses = pick_senses(entries)
        if not senses:
            if spec.kind and card.data.get(spec.kind) == "phrase":  # 短语、句型词典里常常没有，不一定是错字
                stats[_hold(card, UNCHECKED, note="词典里查不到这个短语，意思没有核对，已取消勾选，请确认")] += 1
            elif getattr(dictionary,"known",lambda w: False)(word): #单词没认错，只是词典没收录
                stats[_hold(card, UNCHECKED,
                note="词典没有这个词的释义，单词本身没有认错已经取消勾选，请确认释义")] += 1
            else:
                stats[_hold(card, NOT_FOUND, word=lookup_key(word), note="词典里没有这个词，可能是 OCR 认错了，已取消勾选，请核对")] += 1
            continue 
        if meaning_language == "英语":
            senses = [(e,s if s.trans_word else replace(s,trans_word=s.definition)) for e,s in senses]
        hits = [i for i, (_, s) in enumerate(senses) if matches(meaning, s)]
        sentence = str(card.data.get(spec.sentence) or "") if spec.sentence else ""
        first = next((x for x in senses if x[1].trans_word),None)
        if senses[0][0].source in DIRECT_SOURCES or meaning_language == "英语": 
            if hits:
                _mark(card, VERIFIED, **_found(*senses[hits[0]]))
                stats[VERIFIED] += 1
            elif first is None:
                stats[_hold(card, UNCHECKED, note="词典里没有这种语言的释义，已取消勾选，请确认释义")] += 1
            else:
                card.data[spec.meaning] = first[1].trans_word
                card.fields = template.render_card_fields(card.data, card.context)
                stats[_mark(card, CORRECTED, **_found(*first), original=meaning,
                            note="释义和词典对不上，已换成词典的第一个义项")] += 1
        elif hits and (len(senses) == 1 or not sentence):
            _mark(card, VERIFIED, **_found(*senses[hits[0]]))
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
            stats[_apply_choice(card, template, spec, senses, hits, choices.get(k), verdicts.get(k),
                                dictionary, meaning_language)] += 1
        done = min(start + BATCH, len(pending))
        say(ProgressEvent(CHECK, done, len(pending), f"正在核对多义词的义项：{done}/{len(pending)}"))
    for card in cards:  # 词典管不到的语言、中途取消：没核对过的卡都不能默认进 Anki
        if "dict_check" not in card.context:
            stats[_hold(card, UNCHECKED, note="没有经过词典核对，已取消勾选，请确认")] += 1
    return stats


def _confirmed_by_dictionary(dictionary, word: str, sense: str, fix: str, meaning_language: str) -> bool:
    """AI 把英语义项译成韩语后反查基础词典：韩语词的英语译词要对得上这个单词或义项。其他语言没有反查手段，不算确认。"""
    if meaning_language != "韩语" or dictionary is None:
        return False
    target = f"{word} {sense}".casefold()
    for gloss in glosses(fix):
        try:
            entries = dictionary.lookup(gloss, "英语")
        except DictError as exc:
            log.warning("反查词典失败：%s", exc)
            return False
        for _, found in pick_senses(entries):
            for part in re.split(r"[;,]", _translation(found) or found.definition):
                if part.strip() and re.search(rf"\b{re.escape(part.strip().casefold())}\b", target):
                    return True
    return False


def _translation(sense: Sense) -> str:
    return sense.trans_word or sense.trans_dfn


def _apply_choice(card: Card, template, spec, senses: list[tuple[Entry, Sense]], hits: list[int],
                  choice: int | None, verdict: dict | None = None, dictionary=None,
                  meaning_language: str = "中文") -> str:
    meaning = str(card.data.get(spec.meaning) or "")
    if choice is None:  # 模型没给出选择：只能看字面
        if hits:
            return _mark(card, VERIFIED, **_found(*senses[hits[0]]), note="未经语境确认")
        if not any(_translation(s) for _, s in senses):  # 词典只有外语释义，字面没法比
            return _hold(card, ERROR, note="词典只有英语释义，需要大模型对照，这次没有核对")
        return _hold(card, UNMATCHED, note="释义和词典的义项都对不上")
    if choice == 0:
        return _hold(card, UNMATCHED, note="词典里没有符合原句的义项，已取消勾选，请核对")
    entry, sense = senses[choice - 1]
    found = _found(entry, sense)
    if choice - 1 in hits:
        return _mark(card, VERIFIED, **found)
    replacement, note = _translation(sense), ""
    if not replacement:  # 这个义项没有这种语言的译词：看模型对照词典义项的判断
        if verdict and verdict["same"]:
            return _mark(card, VERIFIED, **found, note="由 AI 对照词典义项确认")
        if sense.native:  # 词典释义就是这种语言：直接用词典的话，不让模型另写
            replacement = sense.definition
        elif not (verdict and verdict["fix"]) or glosses(verdict["fix"]) == glosses(meaning):  # 说不对又给回原释义
            return _hold(card, UNMATCHED, **found, note="语境对应的义项没有译文，请核对")
        elif not _confirmed_by_dictionary(dictionary, str(card.data.get(spec.word)), sense.definition, verdict["fix"], meaning_language):
            # 例如 postpone 被译成「연루하다」（被牵连）：AI 写的话词典没认可，不能放行
            card.data[spec.meaning] = verdict["fix"]
            card.fields = template.render_card_fields(card.data, card.context)
            return _hold(card, UNMATCHED, **found, original=meaning, note="AI 译的释义反查词典对不上，已取消勾选，请核对")
        else:
            replacement, note = verdict["fix"], "由 AI 按词典义项译出"
    card.data[spec.meaning] = replacement
    card.fields = template.render_card_fields(card.data, card.context)
    return _mark(card, CORRECTED, **found, original=meaning, note=note)


def _describe(entry: Entry, sense: Sense) -> str:
    trans = f" {sense.trans_word}" if sense.trans_word else ""
    return f"{entry.label} ({entry.pos}) {sense.order}.{trans}：{sense.definition}"


def _found(entry: Entry, sense: Sense) -> dict:
    return {"sense": _describe(entry, sense), "link": entry.link, "source": entry.source, "example": sense.example,
            "target_code": entry.target_code, "sense_order": sense.order}


def _hold(card: Card, status: str, **info) -> str:
    """没有得到词典确认的卡：取消勾选，用户看过才能进 Anki。"""
    card.selected = False
    return _mark(card, status, **info, deselected=True)


def _mark(card: Card, status: str, **info) -> str:
    card.context["dict_check"] = {"status": status, **{k: v for k, v in info.items() if v not in ("", None)}}
    return status


# ---------------------------------------------------------------------- 配例句
EXAMPLE_SYSTEM = ("你是外语例句编辑。对每个单词：给了 example 就只把它翻译成{language}写进 translation；"
                  "没给就严格按 meaning 这个意思（dictionary_sense 是词典的解释）用 word 本身造一个母语者日常会说的"
                  "自然例句写进 example（可以变形，但不能换成被动、使动等别的词），再翻译成{language}写进 translation。"
                  "不要解释，只输出一个 json 对象。")


def write_examples(llm: ChatModel, jobs: list[dict], meaning_language: str) -> dict[int, dict]:
    """jobs: [{"id", "word", "meaning", "dictionary_sense", "example"（可没有）}] → {id: {"example", "translation"}}。"""
    user = ('逐个处理，输出格式：{"results": [{"id": 1, "example": "…", "translation": "…"}]}\n'
            + json.dumps(jobs, ensure_ascii=False))
    content, _ = llm.chat([{"role": "system", "content": EXAMPLE_SYSTEM.format(language=meaning_language)},
                           {"role": "user", "content": user}])
    results = {}
    for r in parse_json_object(content).get("results") or []:
        try:
            results[int(r["id"])] = {"example": str(r.get("example") or "").strip(),
                                     "translation": str(r.get("translation") or "").strip()}
        except (KeyError, TypeError, ValueError):
            continue
    return results


def _dictionary_example(card: Card, word: str, dictionary, meaning_language: str) -> str:
    check = card.context.get("dict_check") or {}
    example = check.get("example", "")
    if not example and check.get("target_code") and dictionary is not None:
        try:
            example = dictionary.example(word, check["target_code"], check.get("sense_order", 0), meaning_language)
        except DictError as exc:
            log.warning("查词典例句失败：%s", exc)
    return example if find_term(example, lookup_key(word))[0] != -1 else ""  # 例句里得有这个词


def settle_examples(cards: list[Card], template, dictionary, llm: ChatModel, meaning_language: str = "中文",
                    params: dict | None = None, progress: Callable[[ProgressEvent], None] | None = None,
                    cancel: threading.Event | None = None, usage: Usage | None = None) -> None:
    """释义定下来之后配例句，就地修改卡片。模板没声明 example 时什么都不做。"""
    spec = template.spec.output.dictionary
    if spec is None or not spec.example:
        return
    if usage is not None:
        llm = _Metered(llm, usage)
    say = progress or (lambda event: None)
    own_language = lambda word: LANGUAGE_CODES.get(meaning_language) == detect_language(word)  # 不用翻译
    when = template.fields[spec.example].when
    wanted = not when or (params or {}).get(when, True)
    todo = [c for c in cards if wanted and not (spec.keep_sentence and c.data.get(spec.sentence))]

    for start in range(0, len(todo), BATCH):
        if cancel is not None and cancel.is_set():
            return
        batch = todo[start:start + BATCH]
        jobs = []
        for k, card in enumerate(batch):
            word = str(card.data.get(spec.word) or "")
            job = {"id": k, "word": word, "meaning": str(card.data.get(spec.meaning) or ""),
                   "dictionary_sense": (card.context.get("dict_check") or {}).get("sense", "")}
            example = _dictionary_example(card, word, dictionary, meaning_language)
            jobs.append({**job, "example": example} if example else job)
        need = [job for job in jobs if "example" not in job or not own_language(job["word"])]
        try:
            written = write_examples(llm, need, meaning_language) if need else {}
        except Exception as exc:  # 配不上例句不影响出卡
            log.warning("请模型配例句失败：%s", exc)
            written = {}
        for k, (job, card) in enumerate(zip(jobs, batch)):
            reply = written.get(k, {})
            card.data[spec.example] = job.get("example") or reply.get("example", "")
            card.data[spec.example_translation] = "" if own_language(job["word"]) else reply.get("translation", "")
            card.data["example_source"] = "dict" if job.get("example") else "ai"
            card.fields = template.render_card_fields(card.data, card.context)
        done = min(start + BATCH, len(todo))
        say(ProgressEvent(CHECK, done, len(todo), f"正在配例句：{done}/{len(todo)}"))


def real_typos(suspect: str) -> str:
    """模型标出来的疑似错字里，只留真的改变了字的：@、③、页码、空格这些不影响单词，不值得拦下。"""
    letters = lambda text: "".join(ch for ch in text if ch.isalpha())
    kept = []
    for part in re.split(r"[;；]", suspect):
        old, _, new = part.partition("→")
        if letters(old) and letters(new) != letters(old) and (letters(new) or "→" not in part):
            kept.append(part.strip())
    return "；".join(kept)


def flag_ocr_suspects(cards: list[Card]) -> None:
    """分类时模型顺手标出的疑似错字（不多花一次调用）：取消勾选，由用户决定怎么处理，错字不能悄悄进 Anki。"""
    for card in cards:
        suspect = real_typos(str(card.data.get("ocr_suspect") or ""))
        if suspect:
            card.selected = False
            old = {k: v for k, v in (card.context.get("dict_check") or {}).items() if k != "status"}
            _mark(card, OCR_SUSPECT, **{**old, "note": f"疑似 OCR 错字：{suspect}", "deselected": True})


def check_status(card: Card) -> str:
    return (card.context.get("dict_check") or {}).get("status", "")


def pending_cards(cards: list[Card]) -> list[Card]:
    """「待确认」：词典没能确认、也还没有被用户勾选的卡。"""
    return [c for c in cards if not c.selected and check_status(c) in HELD]


def summarize(cards: list[Card]) -> Counter:
    return Counter(s for s in (check_status(c) for c in cards) if s)
