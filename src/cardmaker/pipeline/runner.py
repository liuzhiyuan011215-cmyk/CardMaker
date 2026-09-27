"""生成主流程：提取文字 → 分块并存入数据库 → 并发调用模型（每完成一页立即保存）→ 按书中顺序汇总卡片。

- 并发：线程池同时处理多页（默认 5 页）；
- 断点续传：每页的结果写入 SQLite，任务中断后 resume_task 只处理还没完成或失败的页；
- 顺序一致：不管哪页先完成，最后都按书中顺序做原文校验、去重（保留首次出现）和数量截断，
  所以结果和逐页顺序处理完全一样。
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from ..ingest.pdf import DocumentText, extract_pdf, read_text_file
from ..llm.client import ChatModel, FatalLLMError
from ..progress import GENERATE, Cancelled, ProgressEvent, ProgressFn
from ..storage import (CANCELLED, DONE, FAILED, INCOMPLETE, OK, PENDING, RUNNING, SKIPPED, ChunkRecord, TaskInfo,
                       TaskStore)
from ..templates.loader import OutputError, Template, load_template
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
    concurrency: int = 1  # 同时处理几页
    max_attempts: int = 4  # 每页最多调用几次：第一次 + 最多 3 次重试（指数退避）
    min_chars: int = 30  # 少于这么多字的页跳过

    @classmethod
    def from_dict(cls, data: dict) -> "GenerateOptions":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


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


# ---------------------------------------------------------------------- 单页
def generate_chunk(llm: ChatModel, template: Template, chunk: Chunk, variables: dict,
                   max_attempts: int = 4) -> tuple[ChunkOutcome, list[dict]]:
    """处理一页。调用失败按指数退避重试（2、4、8 秒）；输出不合格就把错误告诉模型让它改。
    多次失败只标记这一页失败，不影响其他页。Key 错误之类的致命错误直接抛出。"""
    outcome = ChunkOutcome(chunk.page_label, FAILED)
    messages = template.build_messages(variables)
    for attempt in range(1, max_attempts + 1):
        outcome.attempts = attempt
        try:
            content, usage = llm.chat(messages)
        except FatalLLMError:
            raise  # 每页都会一样失败，由上层结束任务
        except Exception as exc:
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
        outcome.status, outcome.error = OK, ""
        outcome.raw_items, outcome.invalid = len(items) + len(errors), len(errors)
        return outcome, items
    return outcome, []


# ---------------------------------------------------------------------- 汇总
def build_cards(template: Template, title: str, records: list[ChunkRecord],
                max_cards: int = 0) -> tuple[list[Card], Counter, bool]:
    """按书中顺序把各页的结果变成卡片：原文校验 → 去重（保留首次出现）→ 数量截断。"""
    cards: list[Card] = []
    dropped: Counter = Counter()
    seen: set[str] = set()
    stopped_early = False
    for rec in records:
        if rec.status != OK:
            continue
        chunk = rec.chunk
        context = {"source": format_source(title, chunk.chapter, chunk.page_label), "book_title": title,
                   "chapter": chunk.chapter, "page_label": chunk.page_label}
        for item in rec.items:
            # 跨页的句子可能包含上一页末尾，所以校验范围是「上下文 + 本页」
            grounding = check_grounding(item, template.grounded, f"{chunk.context}\n{chunk.text}")
            if grounding is None:
                dropped["原文中找不到"] += 1
                continue
            if template.dedup_key:
                key = dedup_key(item, template.dedup_key)
                if key in seen:
                    dropped["重复"] += 1
                    continue
                seen.add(key)
            if max_cards and len(cards) >= max_cards:
                dropped["超出最大卡片数"] += 1
                stopped_early = True
                continue
            cards.append(Card(template.render_card_fields(item, context), item, chunk.page_label, chunk.chapter,
                              grounding, context=dict(context)))
    invalid = sum(r.invalid for r in records if r.status == OK)
    if invalid:
        dropped["格式不合格"] += invalid
    return cards, dropped, stopped_early


def make_result(task: TaskInfo, template: Template, records: list[ChunkRecord], cards: list[Card],
                dropped: Counter, stopped_early: bool) -> RunResult:
    result = RunResult(Path(task.input_path), task.template_id, template.name, task.params, task.book_title,
                       pages_selected=task.pages_selected, cards=cards, dropped=dropped, elapsed=task.elapsed,
                       stopped_early=stopped_early, cancelled=task.status == CANCELLED, task_id=task.id)
    for rec in records:
        result.usage.add(rec.usage)
        result.chunks.append(ChunkOutcome(rec.chunk.page_label, rec.status, rec.attempts, rec.raw_items,
                                          rec.invalid, error=rec.error or rec.chunk.skip_reason, usage=rec.usage))
    return result


# ---------------------------------------------------------------------- 任务
def prepare_task(store: TaskStore, input_path: Path, template: Template, params: dict, options: GenerateOptions,
                 progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> int:
    """提取文字、分块，存成一个新任务（此时还没调用模型）。提取阶段被取消会抛出 Cancelled。"""
    start = time.monotonic()
    params = template.resolve_params(params)
    doc = load_document(input_path, options, progress or (lambda event: None), cancel)
    title = options.book_title or doc.title
    chunks = make_chunks(doc, min_chars=options.min_chars)
    task_id = store.create_task(input_path, title, template.id, params, dataclasses.asdict(options), len(doc.pages),
                                chunks)
    store.set_task_status(task_id, RUNNING, elapsed_delta=time.monotonic() - start)
    return task_id


def process_task(store: TaskStore, task_id: int, template: Template, llm: ChatModel,
                 progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> RunResult:
    """处理任务里所有「未完成」或「失败」的页（第一次运行和断点续传都用它）。"""
    say = progress or (lambda event: None)
    task = store.get_task(task_id)
    options = GenerateOptions.from_dict(task.options)
    records = store.load_chunks(task_id)
    todo = [r for r in records if r.status in (PENDING, FAILED)]
    remaining = {r.seq for r in todo}  # 本次还没处理完的页
    workers = max(1, options.concurrency)
    store.set_task_status(task_id, RUNNING)
    start = time.monotonic()

    def variables(rec: ChunkRecord) -> dict:
        c = rec.chunk
        return {**task.params, "text": c.text, "context": c.context, "page_label": c.page_label,
                "chapter": c.chapter, "book_title": task.book_title}

    def enough_cards() -> bool:
        """书中顺序连续完成的前缀已经凑够最大卡片数时，后面的页不可能再进入结果，可以停了。"""
        prefix = []
        for rec in records:
            if rec.seq in remaining:
                break
            prefix.append(rec)
        return len(build_cards(template, task.book_title, prefix, options.max_cards)[0]) >= options.max_cards

    stop, fatal, done = "", None, 0
    queue = iter(todo)
    if todo:
        say(ProgressEvent(GENERATE, 0, len(todo), f"开始生成：共 {len(todo)} 页，同时处理 {min(workers, len(todo))} 页"))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="page") as pool:
        running: dict[Future, ChunkRecord] = {}

        def fill() -> None:
            while not stop and len(running) < workers:
                rec = next(queue, None)
                if rec is None:
                    return
                running[pool.submit(generate_chunk, llm, template, rec.chunk, variables(rec),
                                    options.max_attempts)] = rec

        fill()
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                rec = running.pop(future)
                try:
                    outcome, items = future.result()
                except FatalLLMError as exc:  # 这一页保持原状态，修好设置后可以续传
                    fatal, stop = fatal or exc, "fatal"
                    continue
                rec.status, rec.attempts, rec.error = outcome.status, rec.attempts + outcome.attempts, outcome.error
                rec.raw_items, rec.invalid, rec.items = outcome.raw_items, outcome.invalid, items
                rec.usage.add(outcome.usage)
                store.save_chunk(task_id, rec)  # 断点：这一页的结果已经落盘
                remaining.discard(rec.seq)
                done += 1
                cards_now = len(build_cards(template, task.book_title, records, options.max_cards)[0])
                message = (f"第 {rec.chunk.page_label} 页：模型给出 {rec.raw_items} 条（{outcome.usage.prompt_tokens}"
                           f"+{outcome.usage.completion_tokens} token）" if rec.status == OK else
                           f"第 {rec.chunk.page_label} 页失败（已尝试 {outcome.attempts} 次）：{rec.error}")
                say(ProgressEvent(GENERATE, done, len(todo), message, cards_now))
            if not stop and cancel is not None and cancel.is_set():
                stop = "cancel"
            if not stop and options.max_cards and enough_cards():
                stop = "max_cards"
            fill()

    if stop == "max_cards":  # 后面的页已经用不上了
        for rec in records:
            if rec.seq in remaining:
                rec.status, rec.chunk.skip_reason = SKIPPED, "已达到最大卡片数"
                store.save_chunk(task_id, rec)
    cards, dropped, stopped_early = build_cards(template, task.book_title, records, options.max_cards)
    store.save_cards(task_id, cards)
    unfinished = any(r.status in (PENDING, FAILED) for r in records)
    status = CANCELLED if stop == "cancel" else INCOMPLETE if unfinished else DONE
    store.set_task_status(task_id, status, time.monotonic() - start, str(fatal or ""))
    if fatal:
        raise fatal
    return make_result(store.get_task(task_id), template, records, cards, dropped,
                       stopped_early or stop == "max_cards")


def run_generation(input_path: Path, template: Template, params: dict, llm: ChatModel,
                   options: GenerateOptions | None = None, progress: ProgressFn | None = None,
                   cancel: threading.Event | None = None, store: TaskStore | None = None) -> RunResult:
    """新建任务并处理。用户取消时返回已经生成的部分（result.cancelled 为 True），之后可以续传。"""
    options = options or GenerateOptions()
    store = store or TaskStore()
    try:
        task_id = prepare_task(store, input_path, template, params, options, progress, cancel)
    except Cancelled:  # 还在读取文字时就取消了，没有可续传的内容
        return RunResult(input_path, template.id, template.name, template.resolve_params(params),
                         options.book_title or input_path.stem, cancelled=True)
    return process_task(store, task_id, template, llm, progress, cancel)


def resume_task(store: TaskStore, task_id: int, llm: ChatModel, progress: ProgressFn | None = None,
                cancel: threading.Event | None = None) -> RunResult:
    """断点续传：已经完成的页直接用保存的结果，只处理没完成和失败的页。"""
    task = store.get_task(task_id)
    return process_task(store, task_id, load_template(task.template_id), llm, progress, cancel)


def load_result(store: TaskStore, task_id: int) -> tuple[RunResult, Template]:
    """打开历史任务：读取保存的卡片（包括用户的修改和导入 Anki 的结果），不调用模型。"""
    task = store.get_task(task_id)
    template = load_template(task.template_id)
    records = store.load_chunks(task_id)
    options = GenerateOptions.from_dict(task.options)
    _, dropped, stopped_early = build_cards(template, task.book_title, records, options.max_cards)
    return make_result(task, template, records, store.load_cards(task_id), dropped, stopped_early), template
