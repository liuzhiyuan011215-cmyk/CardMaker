"""界面语言：中文（默认）/ 한국어。

做法：界面上显示文字的地方都套一层 tr()，中文原文就是键。查不到翻译就原样显示，
所以漏翻的地方顶多还是中文，不会出错。程序内部的值（例如释义语言「中文」）不翻，只翻显示。
后台模块产生的进度、报错不用改：键里的 {} 表示可变部分，按句式整句翻，可变部分再翻一遍
（报错里套着报错也能翻）；多行文字逐行翻；用「，」「；」连起来的几段话分段翻。
"""
from __future__ import annotations

import re

from .i18n_ko import KO

LANGUAGES = [("中文", "zh"), ("한국어", "ko")]  # 设置窗口里的选项：(显示, 代码)
LANGUAGE = "zh"


def set_language(language: str) -> None:
    global LANGUAGE
    LANGUAGE = "ko" if language == "ko" else "zh"


def _compile(key: str) -> re.Pattern:
    return re.compile("(.*?)".join(re.escape(part) for part in key.split("{}")), re.S)


_PATTERNS = [(_compile(key), value) for key, value in KO.items() if "{}" in key]


def tr(text):
    """中文 → 当前界面语言。不是文字（例如图标）或查不到时原样返回。"""
    if LANGUAGE != "ko" or not isinstance(text, str) or not text.strip():
        return text
    found = KO.get(text)
    if found is not None:
        return found
    for pattern, value in _PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return value.format(*(tr(part) for part in match.groups()))
    if "\n" in text:
        return "\n".join(tr(line) for line in text.split("\n"))
    for sep, joiner in (("，", ", "), ("；", "; "), ("、", ", ")):
        parts = text.split(sep)
        if len(parts) > 1:
            done = [tr(part) for part in parts]
            if done != parts:  # 一段都翻不了（例如书名里带逗号）就原样显示
                return joiner.join(done)
    return text
