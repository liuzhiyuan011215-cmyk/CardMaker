"""后台任务：生成过程可能要几分钟，放在后台线程跑，界面不会卡住。

线程用 Python 的守护线程（关闭程序时不会被卡住）；结果通过 Qt 信号传回界面线程，
Qt 会自动把跨线程的信号排队到界面线程执行，所以界面代码不用关心线程安全。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..anki.connect import AnkiConnectError
from ..tts import TTSError
from ..config import Settings
from ..llm.client import ChatModel, FatalLLMError, LLMClient, explain_error
from ..pipeline.runner import GenerateOptions, resume_task, run_generation
from ..storage import TaskStore
from ..templates.loader import Template

log = logging.getLogger(__name__)


class BackgroundTask(QObject):
    finished = Signal(object)
    failed = Signal(str)  # 给用户看的错误说明

    def __init__(self):
        super().__init__()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name=type(self).__name__, daemon=True)
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(self) -> None:
        try:
            result = self.work()
        except FatalLLMError as exc:
            self.failed.emit(str(exc))
        except (AnkiConnectError, TTSError) as exc:  # 没开 Anki、没网之类的预期情况，记一行日志即可
            log.info("Anki：%s", exc)
            self.failed.emit(str(exc))
        except Exception as exc:
            log.exception("后台任务失败")
            self.failed.emit(explain_error(exc))
        else:
            self.finished.emit(result)

    def work(self):
        raise NotImplementedError


class FunctionTask(BackgroundTask):
    """在后台执行一个普通函数，比如「测试连接」。"""

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.fn = fn

    def work(self):
        return self.fn()


class ProgressTask(BackgroundTask):
    """在后台执行 fn(report)，fn 可以随时调用 report("…") 报告进度（例如「正在生成发音 3/20」）。"""

    progress = Signal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], object]):
        super().__init__()
        self.fn = fn

    def work(self):
        return self.fn(self.progress.emit)


class GenerateWorker(BackgroundTask):
    progress = Signal(object)  # ProgressEvent

    def __init__(self, input_path: Path | None, template: Template | None, params: dict,
                 options: GenerateOptions | None, settings: Settings, llm: ChatModel | None = None,
                 store: TaskStore | None = None):
        super().__init__()
        self.input_path, self.template, self.params = input_path, template, params
        self.options, self.settings, self.llm, self.store = options, settings, llm, store
        self.resume_task_id: int | None = None
        self._cancel = threading.Event()

    @classmethod
    def for_resume(cls, task_id: int, settings: Settings, llm: ChatModel | None = None,
                   store: TaskStore | None = None) -> "GenerateWorker":
        """断点续传：继续处理某个任务里没完成的页。"""
        worker = cls(None, None, {}, None, settings, llm=llm, store=store)
        worker.resume_task_id = task_id
        return worker

    def cancel(self) -> None:
        self._cancel.set()

    def work(self):
        llm = self.llm or LLMClient.from_settings(self.settings.llm)
        store = self.store or TaskStore()
        # 取消时会返回已生成的部分（任务保存在数据库里，之后可以继续），而不是抛异常
        if self.resume_task_id is not None:
            return resume_task(store, self.resume_task_id, llm, progress=self.progress.emit, cancel=self._cancel)
        return run_generation(self.input_path, self.template, self.params, llm, self.options,
                              progress=self.progress.emit, cancel=self._cancel, store=store)
