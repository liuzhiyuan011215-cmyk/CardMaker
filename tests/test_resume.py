"""并发生成、断点续传、历史记录（全部用假模型，不花 token）。"""
import json
import threading

import pytest

from cardmaker.llm.client import FatalLLMError, Usage
from cardmaker.pipeline import runner
from cardmaker.pipeline.runner import GenerateOptions, load_result, resume_task, run_generation
from cardmaker.storage import CANCELLED, DONE, INCOMPLETE, OK, PENDING, TaskStore
from cardmaker.templates.loader import load_template

S1 = "공유 경제가 빠르게 확산되고 있다."
S2 = "차는 주차장에 방치되기 마련이다."
S3 = "책임 소재가 불분명한 경우가 적지 않다."


class PageFakeLLM:
    """按页面上的标记（PAGE1、PAGE2…）返回预设内容；可以让某页变慢或出错。线程安全。"""

    def __init__(self, replies: dict, delays: dict | None = None):
        self.replies, self.delays = replies, delays or {}
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def chat(self, messages):
        body = messages[1]["content"].rsplit("【本页正文】", 1)[-1]  # 提示词里还有上一页末尾，只看本页
        page = next(key for key in self.replies if key in body)
        with self._lock:
            self.calls.append(page)
        threading.Event().wait(self.delays.get(page, 0))  # 模拟这一页比较慢
        reply = self.replies[page]
        if isinstance(reply, Exception):
            raise reply
        return reply, Usage(prompt_tokens=100, completion_tokens=50, calls=1)


def reply(*pairs):
    return json.dumps({"items": [{"word": w, "meaning": "释义", "source_sentence": s} for w, s in pairs]},
                      ensure_ascii=False)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)  # 重试时不真的等


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


@pytest.fixture
def pdf(make_pdf):
    return make_pdf([f"PAGE1 {S1}", f"PAGE2 {S1} {S2}", f"PAGE3 {S3}"])


@pytest.fixture
def template():
    return load_template("vocab_reading")


def fronts(result):
    return [(c.fields["Front"], c.page_label) for c in result.cards]


def test_concurrent_run_keeps_book_order_and_first_occurrence(pdf, template, store):
    # 第 1 页最慢，第 2、3 页先完成；结果仍按书中顺序，重复的词保留第 1 页的
    llm = PageFakeLLM({"PAGE1": reply(("확산되다", S1)),
                       "PAGE2": reply(("확산되다", S1), ("방치되다", S2)),
                       "PAGE3": reply(("불분명하다", S3))}, delays={"PAGE1": 0.3})
    result = run_generation(pdf, template, {}, llm, GenerateOptions(concurrency=3, min_chars=5), store=store)
    assert llm.calls[0] == "PAGE1" and set(llm.calls) == {"PAGE1", "PAGE2", "PAGE3"}
    assert fronts(result) == [("확산되다", "1"), ("방치되다", "2"), ("불분명하다", "3")]
    assert result.dropped["重复"] == 1
    assert store.get_task(result.task_id).status == DONE


def test_resume_only_redoes_failed_pages(pdf, template, store):
    first_llm = PageFakeLLM({"PAGE1": reply(("확산되다", S1)), "PAGE2": ConnectionError("网络断了"),
                             "PAGE3": reply(("불분명하다", S3))})
    first = run_generation(pdf, template, {}, first_llm, GenerateOptions(concurrency=2, max_attempts=2, min_chars=5),
                           store=store)
    assert [c.status for c in first.chunks] == [OK, "failed", OK]
    assert store.get_task(first.task_id).status == INCOMPLETE

    second_llm = PageFakeLLM({"PAGE1": reply(), "PAGE2": reply(("방치되다", S2)), "PAGE3": reply()})
    second = resume_task(store, first.task_id, second_llm)
    assert second_llm.calls == ["PAGE2"]  # 已完成的页不再调用模型
    assert fronts(second) == [("확산되다", "1"), ("방치되다", "2"), ("불분명하다", "3")]
    assert store.get_task(first.task_id).status == DONE
    # 两次运行的用量累计：第 1、3 页各 1 次，续传时第 2 页 1 次；网络失败的请求没有回复，不消耗 token
    assert second.usage.calls == 3


def test_cancel_then_resume(pdf, template, store):
    cancel = threading.Event()
    llm = PageFakeLLM({"PAGE1": reply(("확산되다", S1)), "PAGE2": reply(("방치되다", S2)),
                       "PAGE3": reply(("불분명하다", S3))})

    def progress(event):
        if event.stage == "generate" and event.done == 1:
            cancel.set()  # 第一页完成后点了取消

    first = run_generation(pdf, template, {}, llm, GenerateOptions(min_chars=5), progress=progress, cancel=cancel,
                           store=store)
    assert first.cancelled and fronts(first) == [("확산되다", "1")]
    assert [c.status for c in first.chunks] == [OK, PENDING, PENDING]
    assert store.get_task(first.task_id).status == CANCELLED
    assert "未处理 2（可继续）" in first.summary_text()

    second = resume_task(store, first.task_id, llm)
    assert llm.calls == ["PAGE1", "PAGE2", "PAGE3"]
    assert len(second.cards) == 3 and not second.cancelled


def test_fatal_error_stops_task_and_can_resume(pdf, template, store):
    bad = PageFakeLLM({"PAGE1": FatalLLMError("API Key 不正确"), "PAGE2": FatalLLMError("API Key 不正确"),
                       "PAGE3": FatalLLMError("API Key 不正确")})
    with pytest.raises(FatalLLMError):
        run_generation(pdf, template, {}, bad, GenerateOptions(min_chars=5), store=store)
    task = store.list_tasks()[0]
    assert task.status == INCOMPLETE and "API Key" in task.error and task.unfinished
    assert len(bad.calls) == 1  # 第一次就知道 Key 不对，没有继续请求其他页

    good = PageFakeLLM({"PAGE1": reply(("확산되다", S1)), "PAGE2": reply(), "PAGE3": reply()})
    result = resume_task(store, task.id, good)
    assert len(result.cards) == 1 and store.get_task(task.id).status == DONE


def test_history_keeps_user_edits(pdf, template, store):
    llm = PageFakeLLM({"PAGE1": reply(("확산되다", S1)), "PAGE2": reply(), "PAGE3": reply()})
    result = run_generation(pdf, template, {}, llm, GenerateOptions(min_chars=5), store=store)
    card = result.cards[0]
    card.data["meaning"] = "扩散，蔓延"
    card.fields = template.render_card_fields(card.data, card.context)
    card.selected, card.anki_status = False, "已导入"
    store.save_cards(result.task_id, result.cards)

    loaded, loaded_template = load_result(store, result.task_id)
    assert loaded_template.id == "vocab_reading"
    assert loaded.cards[0].fields["Meaning"] == "扩散，蔓延"
    assert loaded.cards[0].selected is False and loaded.cards[0].anki_status == "已导入"
    assert loaded.task_id == result.task_id and loaded.usage.calls == 3
