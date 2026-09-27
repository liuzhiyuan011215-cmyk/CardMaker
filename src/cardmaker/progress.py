"""进度与取消：核心流程通过回调报告进度，界面据此更新进度条，命令行则直接打印。"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

EXTRACT, GENERATE = "extract", "generate"


@dataclass
class ProgressEvent:
    stage: str  # extract：提取文字（含 OCR）；generate：生成卡片
    done: int  # 本阶段已完成的页（块）数
    total: int  # 本阶段总页（块）数
    message: str
    cards: int = 0  # 目前已生成的卡片数


ProgressFn = Callable[[ProgressEvent], None]


class Cancelled(Exception):
    """用户点了「取消」。"""


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()
