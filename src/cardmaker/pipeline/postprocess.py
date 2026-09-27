"""模型输出的后处理：原文校验（防编造）、去重、数量上限。"""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from difflib import SequenceMatcher

APPROX_RATIO = 0.85  # 模糊匹配阈值：OCR 个别字认错、模型顺手改正时仍能通过
EXACT, APPROX = "exact", "approx"


def normalize(text: str) -> str:
    """比较用的规范化：统一全角半角（NFKC）、忽略大小写和所有空白。

    NFKC 也会把分解形式的韩文字母（有些 PDF 提取出来是这样）合成为正常的音节。
    """
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s*|\n+")
_TOKEN_SPLIT = re.compile(r"[\s,.;:!?，。；：！？()\[\]（）「」“”\"']+")


def locate(value: str, source: str) -> str | None:
    """在原文中查找 value。返回 'exact'（原样出现）、'approx'（高度相似）或 None（找不到）。"""
    target = normalize(value)
    if not target:
        return EXACT
    haystack = normalize(source)
    if target in haystack:
        return EXACT
    if len(target) <= 3:  # 太短的内容模糊匹配没有意义
        return None
    if len(target) <= 12:  # 单词：和原文里的每个词比
        spans = _TOKEN_SPLIT.split(source)
    else:  # 句子：和原文里的每个句子（以及相邻两句拼起来）比
        sentences = [s for s in _SENTENCE_SPLIT.split(source) if s and s.strip()]
        spans = sentences + [a + b for a, b in zip(sentences, sentences[1:])]
    best = max((SequenceMatcher(None, target, normalize(s)).ratio() for s in spans if s), default=0.0)
    return APPROX if best >= APPROX_RATIO else None


CLOZE = re.compile(r"\{\{c\d+::(.*?)(?:::(.*?))?\}\}", re.S)


def cloze_answers(text: str) -> list[str]:
    """取出 {{c1::答案}} 或 {{c1::答案::提示}} 里的答案。"""
    return [m.group(1) for m in CLOZE.finditer(text)]


def check_grounding(item: dict, fields: list[str], source: str, cloze_fields: list[str] = ()) -> str | None:
    """检查条目中所有需要校验的字段。全部原样出现 → exact；有模糊匹配 → approx；任一找不到 → None。

    cloze_fields：填空卡的挖空答案必须来自原文（这些字段合起来至少要有一处挖空）。
    """
    values = [str(item.get(name) or "") for name in fields]
    answers = [a for name in cloze_fields for a in cloze_answers(str(item.get(name) or ""))]
    if cloze_fields and not answers:
        return None
    result = EXACT
    for value in values + answers:
        found = locate(value, source)
        if found is None:
            return None
        if found == APPROX:
            result = APPROX
    return result


def add_mcq_options(item: dict) -> dict:
    """选择题：把正确答案和 3 个干扰项打乱成 A–D，并记下正确答案的字母。

    用题目做随机种子，同一道题每次打乱的顺序都一样（重新生成、编辑后不会变来变去）。
    """
    options = [str(item.get("correct", "")), *[str(d) for d in item.get("distractors", [])[:3]]]
    order = list(range(len(options)))
    random.Random(hashlib.sha1(str(item.get("question", "")).encode("utf-8")).hexdigest()).shuffle(order)
    result = dict(item)
    for letter, index in zip("abcd", order):
        result[f"option_{letter}"] = options[index]
    result["answer"] = "ABCD"[order.index(0)]
    return result


def dedup_key(item: dict, key: str) -> str:
    return normalize(str(item.get(key, "")))
