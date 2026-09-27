"""日志：详细日志写入文件（方便排查），终端只显示警告以上。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import log_dir

_configured = False


def setup_logging(verbose: bool = False) -> Path:
    global _configured
    path = log_dir() / "cardmaker.log"
    if _configured:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console)

    # 第三方库的调试日志太多，且可能包含请求细节，只保留警告以上
    for name in ("httpx", "httpcore", "openai", "RapidOCR"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    return path
