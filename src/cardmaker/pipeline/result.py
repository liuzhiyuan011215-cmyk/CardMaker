"""生成结果的数据结构。界面、导出、Anki 写入都只依赖这里。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..llm.client import Usage


def cost_of(usage: Usage, options: dict) -> float | None:
    """按生成时记下的单价（元 / 百万 token）算费用：本地模型是 0；没填单价返回 None，只显示 token。"""
    if options.get("local_model"):
        return 0.0
    price_in, price_out = options.get("price_input_per_m") or 0, options.get("price_output_per_m") or 0
    if not (price_in or price_out):
        return None
    return (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1_000_000


def money(value: float) -> str:
    """费用显示：几张卡往往只要零点几分钱，所以不到 1 元时多留几位小数。"""
    return f"{value:,.2f}" if value >= 1 else f"{value:.6f}".rstrip("0").rstrip(".")


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
    status: str  # ok / failed / skipped / pending（取消后尚未处理）
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
    task_id: int | None = None  # 数据库里的任务编号（断点续传、历史记录用）
    options: dict = field(default_factory=dict)  # 生成选项（含模型名和单价，算费用用）
    check_usage: Usage = field(default_factory=Usage)  # usage 里词典核对的部分

    def count(self, status: str) -> int:
        return sum(1 for c in self.chunks if c.status == status)

    @property
    def cost(self) -> float | None:
        return cost_of(self.usage, self.options)

    def summary_lines(self) -> list[str]:
        level = self.params.get("level")
        lines = [f"《{self.book_title}》 · 模板「{self.template_name}」" + (f" · 水平 {level}" if level else "")]
        pending = f"，未处理 {self.count('pending')}（可继续）" if self.count("pending") else ""
        lines.append(f"页面：共选 {self.pages_selected} 页，成功 {self.count('ok')}，失败 {self.count('failed')}，"
                     f"跳过 {self.count('skipped')}{pending}")
        kinds = Counter(c.data.get("kind") for c in self.cards if c.data.get("kind"))
        kind_text = "、".join(f"{'单词' if k == 'word' else '短语/句型'} {n}" for k, n in kinds.items())
        drop_text = "，".join(f"{reason} {n}" for reason, n in self.dropped.items())
        lines.append(f"卡片：{len(self.cards)} 张" + (f"（{kind_text}）" if kind_text else "")
                     + (f"；丢弃：{drop_text}" if drop_text else ""))
        from .meaning_check import LABELS, summarize  # 放在这里避免循环导入
        checks = summarize(self.cards)
        if checks:
            lines.append("词典核对释义：" + "，".join(f"{LABELS[k]} {n}" for k, n in checks.most_common()))
        levels = Counter(c.data.get("difficulty") for c in self.cards if c.data.get("difficulty"))
        if levels:
            lines.append("难度分布：" + " · ".join(f"{k} {n}" for k, n in sorted(levels.items())))
        u = self.usage
        lines.append(f"Token：输入 {u.prompt_tokens:,}（缓存命中 {u.cached_tokens:,}）/ 输出 {u.completion_tokens:,}，"
                     f"共 {u.total_tokens:,}，调用 {u.calls} 次")
        if self.check_usage.calls:
            lines.append(f"其中词典核对：{self.check_usage.total_tokens:,} token，调用 {self.check_usage.calls} 次")
        cost, model = self.cost, self.options.get("model") or "?"
        if self.options.get("local_model"):
            lines.append(f"费用：0 元（本地模型 {model}）")
        elif cost is not None:
            price_in, price_out = (self.options.get(k) or 0 for k in ("price_input_per_m", "price_output_per_m"))
            lines.append(f"费用：约 {money(cost)} 元（{model}；单价 输入 {price_in:g} / 输出 {price_out:g} 元每百万 token）")
        if self.cards:
            n = len(self.cards)
            lines.append(f"平均每张卡：{u.total_tokens / n:,.0f} token，{self.elapsed / n:.1f} 秒"
                         + (f"，约 {money(cost / n)} 元" if cost else ""))
        note = "（已取消，保留已生成的部分）" if self.cancelled else ("（已达到最大卡片数，提前结束）" if self.stopped_early else "")
        lines.append(f"耗时：{self.elapsed:.1f} 秒{note}")
        return lines

    def summary_text(self) -> str:
        return "\n".join(self.summary_lines())
