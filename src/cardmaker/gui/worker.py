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
from ..config import Settings
from ..llm.client import ChatModel, FatalLLMError, LLMClient, explain_error
from ..pipeline.runner import GenerateOptions, run_generation
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
        except AnkiConnectError as exc:  # 没开 Anki 之类的预期情况，记一行日志即可
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


class GenerateWorker(BackgroundTask):
    progress = Signal(object)  # ProgressEvent

    def __init__(self, input_path: Path, template: Template, params: dict, options: GenerateOptions,
                 settings: Settings, llm: ChatModel | None = None):
        super().__init__()
        self.input_path, self.template, self.params = input_path, template, params
        self.options, self.settings, self.llm = options, settings, llm
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def work(self):
        llm = self.llm or LLMClient.from_settings(self.settings.llm)
        # 取消时 run_generation 会返回已生成的部分，而不是抛异常
        return run_generation(self.input_path, self.template, self.params, llm, self.options,
                              progress=self.progress.emit, cancel=self._cancel)
