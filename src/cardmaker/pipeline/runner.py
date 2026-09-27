"""生成主流程：提取文字 → 分块 → 逐块调用模型 → 校验 → 去重 → 结果。

第一阶段按顺序逐块处理；第三阶段会在这里加入并发和 SQLite 断点续传。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..ingest.pdf import DocumentText, extract_pdf, read_text_file
from ..llm.client import ChatModel, FatalLLMError
from ..progress import GENERATE, Cancelled, ProgressEvent, ProgressFn
from ..templates.loader import OutputError, Template
from .chunking import Chunk, make_chunks
from .postprocess import check_grounding, dedup_key
from .result import Card, ChunkOutcome, RunResult

log = logging.getLogger(__name__)


@dataclass
class GenerateOptions:
    pages: list[int] | None = None  # 从 1 开始的页码，None 表示全部
    max_cards: int = 0  # 0 表示不限
    ocr_mode: str = "auto"
    ocr_langs: list[str] = field(default_factory=lambda: ["zh", "ko"])
    dpi: int = 200
    book_title: str | None = None
    max_attempts: int = 3  # 每块最多调用几次（输出不合格或调用失败时重试）
    min_chars: int = 30  # 少于这么多字的页跳过


def load_document(path: Path, options: GenerateOptions, progress: ProgressFn,
                  cancel: threading.Event | None = None) -> DocumentText:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path, pages=options.pages, ocr_mode=options.ocr_mode, ocr_langs=options.ocr_langs,
                           dpi=options.dpi, progress=progress, cancel=cancel)
    if suffix in (".txt", ".md"):
        return read_text_file(path)
    raise ValueError(f"暂不支持的文件类型：{suffix}")


def format_source(title: str, chapter: str, page_label: str) -> str:
    parts = [f"《{title}》"] + ([chapter] if chapter else []) + [f"p.{page_label}"]
    return " · ".join(parts)


def generate_chunk(llm: ChatModel, template: Template, chunk: Chunk, variables: dict,
                   max_attempts: int = 3) -> tuple[ChunkOutcome, list[dict]]:
    """处理一个分块。输出不合格时把错误告诉模型让它改；多次失败只标记这一块失败。"""
    outcome = ChunkOutcome(chunk.page_label, "failed")
    messages = template.build_messages(variables)
    for attempt in range(1, max_attempts + 1):
        outcome.attempts = attempt
        try:
            content, usage = llm.chat(messages)
        except FatalLLMError:
            raise  # Key 错、模型名错之类，每页都会一样失败，直接结束任务
        except Exception as exc:  # SDK 已对网络错误、限流自动重试过，这里再兜底
            outcome.error = f"调用失败：{exc}"
            log.warning("第 %s 页第 %d 次调用失败：%s", chunk.page_label, attempt, exc)
            if attempt < max_attempts:
                time.sleep(min(2 ** attempt, 10))
            continue
        outcome.usage.add(usage)
        try:
            items, errors = template.parse_output(content)
        except OutputError as exc:
            outcome.error = f"输出不合格：{exc}"
            log.warning("第 %s 页第 %d 次输出不合格：%s", chunk.page_label, attempt, exc)
            messages = messages + [
                {"role": "assistant", "content": content[:6000]},
                {"role": "user", "content": f"你的输出不符合要求：{exc}\n请修正后重新输出完整的 json，不要输出其他内容。"},
            ]
            continue
        if errors:
            log.info("第 %s 页有 %d 条不合格，已丢弃：%s", chunk.page_label, len(errors), "；".join(errors))
        outcome.status, outcome.error = "ok", ""
        outcome.raw_items, outcome.invalid = len(items) + len(errors), len(errors)
        return outcome, items
    return outcome, []


def accept_items(items: list[dict], chunk: Chunk, template: Template, title: str,
                 seen: set[str], result: RunResult, max_cards: int) -> list[Card]:
    """原文校验 → 去重 → 数量上限 → 映射成卡片字段。"""
    context = {"source": format_source(title, chunk.chapter, chunk.page_label), "book_title": title,
               "chapter": chunk.chapter, "page_label": chunk.page_label}
    accepted: list[Card] = []
    for item in items:
        # 跨页的句子可能包含上一页末尾，所以校验范围是「上下文 + 本页」
        grounding = check_grounding(item, template.grounded, f"{chunk.context}\n{chunk.text}")
        if grounding is None:
            result.dropped["原文中找不到"] += 1
            log.info("第 %s 页丢弃（原文中找不到）：%s", chunk.page_label, item)
            continue
        if template.dedup_key:
            key = dedup_key(item, template.dedup_key)
            if key in seen:
                result.dropped["重复"] += 1
                continue
            seen.add(key)
        if max_cards and len(result.cards) + len(accepted) >= max_cards:
            result.dropped["超出最大卡片数"] += 1
            result.stopped_early = True
            continue
        fields = template.render_card_fields(item, context)
        accepted.append(Card(fields, item, chunk.page_label, chunk.chapter, grounding, context=dict(context)))
    return accepted


def run_generation(input_path: Path, template: Template, params: dict, llm: ChatModel,
                   options: GenerateOptions | None = None, progress: ProgressFn | None = None,
                   cancel: threading.Event | None = None) -> RunResult:
    """跑完整个任务。用户取消时返回已经生成的部分（result.cancelled 为 True）。"""
    options = options or GenerateOptions()
    say = progress or (lambda event: None)
    start = time.monotonic()
    params = template.resolve_params(params)
    result = RunResult(input_path, template.id, template.name, params, options.book_title or input_path.stem)

    try:
        doc = load_document(input_path, options, say, cancel)
    except Cancelled:
        result.cancelled = True
        result.elapsed = time.monotonic() - start
        return result
    title = result.book_title = options.book_title or doc.title
    result.pages_selected = len(doc.pages)
    chunks, skipped_pages = make_chunks(doc, min_chars=options.min_chars)
    result.chunks.extend(ChunkOutcome(p.label, "skipped", error="字数太少") for p in skipped_pages)
    seen: set[str] = set()

    for n, chunk in enumerate(chunks, 1):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            result.chunks.extend(ChunkOutcome(c.page_label, "skipped", error="已取消") for c in chunks[n - 1:])
            break
        if options.max_cards and len(result.cards) >= options.max_cards:
            result.stopped_early = True
            result.chunks.append(ChunkOutcome(chunk.page_label, "skipped", error="已达到最大卡片数"))
            continue
        say(ProgressEvent(GENERATE, n - 1, len(chunks), f"第 {chunk.page_label} 页（{chunk.method}）：正在生成…",
                          len(result.cards)))
        variables = {**params, "text": chunk.text, "context": chunk.context, "page_label": chunk.page_label,
                     "chapter": chunk.chapter, "book_title": title}
        outcome, items = generate_chunk(llm, template, chunk, variables, options.max_attempts)
        result.usage.add(outcome.usage)
        if outcome.invalid:
            result.dropped["格式不合格"] += outcome.invalid
        cards = accept_items(items, chunk, template, title, seen, result, options.max_cards)
        outcome.kept = len(cards)
        result.cards.extend(cards)
        result.chunks.append(outcome)
        if outcome.status == "ok":
            message = (f"第 {chunk.page_label} 页：模型给出 {outcome.raw_items} 条，保留 {len(cards)} 张"
                       f"（{outcome.usage.prompt_tokens}+{outcome.usage.completion_tokens} token）")
        else:
            message = f"第 {chunk.page_label} 页失败（已尝试 {outcome.attempts} 次）：{outcome.error}"
        say(ProgressEvent(GENERATE, n, len(chunks), message, len(result.cards)))

    result.elapsed = time.monotonic() - start
    return result
