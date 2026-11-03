"""「待确认」的卡再识别一次：只处理这些卡所在的页，不重跑整本书，省钱。

大模型能看前后文，比 OCR 更容易认出错字，但它读出来的结果不直接算数：
必须和原来的字足够接近（不许换成别的词），而且还要再过一遍词典，词典确认了才勾选。
短语、句型不是认错字的问题，只再查一次词典，查不到的留给用户自己看。
"""
from __future__ import annotations

import base64
import difflib
import json
import logging
from collections import defaultdict
from pathlib import Path

import pymupdf

from ..ingest.pdf import METHOD_OCR
from ..llm.client import ChatModel, FatalLLMError, Usage, parse_json_object
from .meaning_check import CORRECTED, VERIFIED, _Metered, check_meanings, check_status, pending_cards, settle_examples
from .result import Card

log = logging.getLogger(__name__)

READ_SYSTEM = ("你是书页校对员。下面的词条是 OCR 从这一页读出来的，个别字可能认错。对照这一页的内容和前后文，"
               "写出书上实际印的 word 和 sentence。只改认错的字，不要改写，也不要换成别的词。"
               "不要解释，只输出一个 json 对象。")
MIN_WORD_RATIO, MIN_SENTENCE_RATIO = 0.5, 0.7
IMAGE_DPI = 150


def page_image(path: Path, label: str) -> bytes | None:
    """扫描页的图片；文件不在了或者不是 PDF 时返回 None，只用文字再校一遍。"""
    try:
        with pymupdf.open(path) as doc:
            for number, page in enumerate(doc, 1):
                if (page.get_label() or str(number)) == label:
                    return page.get_pixmap(dpi=IMAGE_DPI, alpha=False).tobytes("png")
    except Exception as exc:
        log.info("拿不到第 %s 页的图片：%s", label, exc)
    return None


def read_page(llm: ChatModel, text: str, image: bytes | None, jobs: list[dict]) -> dict[int, dict]:
    """jobs: [{"id", "word", "sentence"}] → {id: {"word", "sentence"}}。模型不支持图片时退回只看文字。"""
    prompt = ('逐个校对，输出格式：{"results": [{"id": 1, "word": "…", "sentence": "…"}]}\n'
              + json.dumps(jobs, ensure_ascii=False) + f"\n\n【这一页 OCR 读出来的文字】\n{text}")
    plain = [{"role": "system", "content": READ_SYSTEM}, {"role": "user", "content": prompt}]
    content = None
    if image and not getattr(llm, "local", False):
        url = "data:image/png;base64," + base64.b64encode(image).decode()
        try:
            content, _ = llm.chat([plain[0], {"role": "user", "content": [{"type": "text", "text": prompt},
                                                                          {"type": "image_url", "image_url": {"url": url}}]}])
        except FatalLLMError:
            raise
        except Exception as exc:
            log.info("模型看不了图片，只用文字校对：%s", exc)
    if content is None:
        content, _ = llm.chat(plain)
    results = {}
    for r in parse_json_object(content).get("results") or []:
        try:
            results[int(r["id"])] = {"word": str(r.get("word") or "").strip(), "sentence": str(r.get("sentence") or "").strip()}
        except (KeyError, TypeError, ValueError):
            continue
    return results


def _close(old: str, new: str, ratio: float) -> bool:
    return bool(new) and difflib.SequenceMatcher(None, old, new).ratio() >= ratio


def recheck_cards(cards: list[Card], template, records, input_path: Path, llm: ChatModel, dictionary,
                  meaning_language: str = "中文", params: dict | None = None, usage: Usage | None = None) -> int:
    """就地修改卡片，返回这次通过词典核对、重新勾选的张数。没开词典时什么都不做：没法确认就不能放行。"""
    spec = template.spec.output.dictionary
    todo = pending_cards(cards)
    if spec is None or dictionary is None or not todo:
        return 0
    pages = {rec.chunk.page_label: rec.chunk for rec in records}
    groups: dict[str, list[Card]] = defaultdict(list)
    for card in todo:
        if not (spec.kind and card.data.get(spec.kind) == "phrase") and card.page_label in pages:
            groups[card.page_label].append(card)

    reader = _Metered(llm, usage) if usage is not None else llm
    fixes: dict[int, str] = {}
    for label, group in groups.items():
        chunk = pages[label]
        image = page_image(input_path, label) if chunk.method == METHOD_OCR else None
        jobs = [{"id": k, "word": str(c.data.get(spec.word) or ""),
                 "sentence": str(c.data.get(spec.sentence) or "") if spec.sentence else ""}
                for k, c in enumerate(group)]
        try:
            results = read_page(reader, chunk.text, image, jobs)
        except FatalLLMError:
            raise
        except Exception as exc:  # 这一页读不了，卡片还留在待确认里
            log.warning("重新识别第 %s 页失败：%s", label, exc)
            continue
        for k, card in enumerate(group):
            old, new = jobs[k]["word"], results.get(k, {}).get("word", "")
            if new != old and _close(old, new, MIN_WORD_RATIO):
                card.data[spec.word] = new
                if card.data.get("surface") == old:
                    card.data["surface"] = new
                fixes[id(card)] = f"{old}→{new}"
            sentence = results.get(k, {}).get("sentence", "")
            if spec.sentence and _close(jobs[k]["sentence"], sentence, MIN_SENTENCE_RATIO):
                card.data[spec.sentence] = sentence

    for card in todo:
        card.data.pop("ocr_suspect", None)
        card.context.pop("dict_check", None)
        card.fields = template.render_card_fields(card.data, card.context)
    check_meanings(todo, template, dictionary, llm, meaning_language, usage=usage)
    passed = [c for c in todo if check_status(c) in (VERIFIED, CORRECTED)]
    settle_examples(passed, template, dictionary, llm, meaning_language, params, usage=usage)
    for card in passed:
        card.selected = True
        if id(card) in fixes:
            card.context["dict_check"]["note"] = f"大模型重新识别：{fixes[id(card)]}"
    return len(passed)
