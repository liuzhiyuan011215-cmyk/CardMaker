"""生成结果的数据结构。界面、导出、Anki 写入都只依赖这里。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..llm.client import Usage


@dataclass
class Card:
    fields: dict[str, str]  # 映射后的 Anki 字段
    data: dict  # 校验后的模型输出，保留原始字段，方便单张重新生成
    page_label: str
    chapter: str = ""
    grounding: str = "exact"  # exact：原文原样出现；approx：高度相似（多为 OCR 误差）
    context: dict = field(default_factory=dict)  # 出处等信息，编辑后重新映射字段时要用
    selected: bool = True  # 预览界面里是否勾选（只导入勾选的）
    anki_status: str = ""  # 导入 Anki 后的结果：已导入 / 已存在 / 失败


@dataclass
class ChunkOutcome:
    page_label: str
    status: str  # ok / failed / skipped
    attempts: int = 0
    raw_items: int = 0  # 模型返回的条数
    invalid: int = 0  # 其中格式不合格被丢弃的条数
    kept: int = 0  # 最终保留的条数
    error: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass
class RunResult:
    input_path: Path
    template_id: str
    template_name: str
    params: dict
    book_title: str
    pages_selected: int = 0
    chunks: list[ChunkOutcome] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    dropped: Counter = field(default_factory=Counter)  # 丢弃原因 → 数量
    elapsed: float = 0.0
    stopped_early: bool = False
    cancelled: bool = False

    def count(self, status: str) -> int:
        return sum(1 for c in self.chunks if c.status == status)

    def summary_lines(self) -> list[str]:
        level = self.params.get("level")
        lines = [f"《{self.book_title}》 · 模板「{self.template_name}」" + (f" · 水平 {level}" if level else "")]
        lines.append(f"页面：共选 {self.pages_selected} 页，成功 {self.count('ok')}，失败 {self.count('failed')}，"
                     f"跳过 {self.count('skipped')}")
        kinds = Counter(c.data.get("kind") for c in self.cards if c.data.get("kind"))
        kind_text = "、".join(f"{'单词' if k == 'word' else '短语/句型'} {n}" for k, n in kinds.items())
        drop_text = "，".join(f"{reason} {n}" for reason, n in self.dropped.items())
        lines.append(f"卡片：{len(self.cards)} 张" + (f"（{kind_text}）" if kind_text else "")
                     + (f"；丢弃：{drop_text}" if drop_text else ""))
        levels = Counter(c.data.get("difficulty") for c in self.cards if c.data.get("difficulty"))
        if levels:
            lines.append("难度分布：" + " · ".join(f"{k} {n}" for k, n in sorted(levels.items())))
        u = self.usage
        lines.append(f"Token：输入 {u.prompt_tokens:,}（缓存命中 {u.cached_tokens:,}）/ 输出 {u.completion_tokens:,}，"
                     f"共 {u.total_tokens:,}，调用 {u.calls} 次")
        note = "（已取消，保留已生成的部分）" if self.cancelled else ("（已达到最大卡片数，提前结束）" if self.stopped_early else "")
        lines.append(f"耗时：{self.elapsed:.1f} 秒{note}")
        return lines

    def summary_text(self) -> str:
        return "\n".join(self.summary_lines())
