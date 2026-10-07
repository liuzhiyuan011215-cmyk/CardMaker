"""给大模型用的接口（MCP，Model Context Protocol）：让 Claude、Cursor、ChatGPT 这类 AI 助手直接操作 CardMaker。

用户只要对 AI 说「把这本 PDF 第 3～5 页做成单词卡，导入 Anki」，AI 就会依次调用下面的工具。
只用标准库：通过标准输入 / 输出收发 JSON-RPC 消息（一行一条）。在 AI 助手的 MCP 设置里加一条：

    "cardmaker": {"command": "<项目>/.venv/bin/python", "args": ["-m", "cardmaker.mcp_server"]}

大模型、语音、词典都用 App「设置」里的配置（API Key 不会出现在任何返回内容里）。
"""
from __future__ import annotations

import contextlib
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .progress import ProgressEvent

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # 新的在前
MAX_TEXT = 60_000  # 一次最多返回多少字，太长会撑爆 AI 的上下文
INSTRUCTIONS = """CardMaker 把 PDF（文字版或扫描版）和文本整理成 Anki 单词卡。
常见流程：list_purposes 看能做哪些卡 → generate_cards 生成（花 token，先用 pages 选几页试）
→ get_cards 检查 → import_to_anki 导入（要先打开 Anki）或 export_apkg 导出文件。
只想要 PDF 里的文字（例如让 AI 自己整理、翻译）就用 extract_text，不花 token。"""


@dataclass
class Tool:
    name: str
    description: str
    properties: dict[str, dict]
    required: tuple[str, ...]
    run: Callable[..., str]

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": {"type": "object", "properties": self.properties, "required": list(self.required)}}


def _pages(spec: str | None) -> list[int] | None:
    from .cli import parse_pages

    return parse_pages(spec) if spec else None


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


class CardMakerTools:
    """每个工具一个方法，返回给 AI 看的文字。出错时抛 ValueError 等，由服务器转成 isError 结果。"""

    def __init__(self, store=None, llm_factory: Callable | None = None, anki_factory: Callable | None = None):
        from .config import load_settings
        from .storage import TaskStore

        self.settings = load_settings()
        self.store = store or TaskStore()
        self.llm_factory = llm_factory
        self.anki_factory = anki_factory

    # ---------------------------------------------------------------- 文字
    def extract_text(self, path: str, pages: str = "", ocr: str = "auto", progress=None) -> str:
        from .ingest.pdf import extract_pdf

        file = Path(path).expanduser()
        if not file.exists():
            raise ValueError(f"找不到文件：{file}")
        if file.suffix.lower() != ".pdf":
            text = file.read_text(encoding="utf-8", errors="replace")
        else:
            doc = extract_pdf(file, pages=_pages(pages), ocr_mode=ocr, ocr_langs=self.settings.ocr.langs,
                              dpi=self.settings.ocr.dpi, progress=progress)
            text = f"《{doc.title}》共 {doc.page_count} 页" + "".join(
                f"\n\n===== 第 {p.label} 页（{p.method}）=====\n{p.text}" for p in doc.pages)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + f"\n\n……后面还有 {len(text) - MAX_TEXT} 字没显示，请用 pages 参数分批提取。"
        return text

    # ---------------------------------------------------------------- 用途
    def list_purposes(self, progress=None) -> str:
        from .templates.loader import READY_GROUPS, list_templates

        purposes = []
        for tpl in list_templates():
            if tpl.group in READY_GROUPS:
                params = {name: {"说明": spec.label, "默认": spec.default, **({"可选": spec.choices} if spec.choices else {})}
                          for name, spec in tpl.params.items()}
                purposes.append({"purpose": tpl.id, "名称": tpl.name, "说明": tpl.description, "参数": params})
        return _json(purposes)

    # ---------------------------------------------------------------- 生成
    def generate_cards(self, path: str, purpose: str, pages: str = "", max_cards: int = 0, level: str = "",
                       meaning_language: str = "", title: str = "", progress=None) -> str:
        from .dictionary import make_dictionary
        from .llm.client import make_llm
        from .pipeline.runner import GenerateOptions, run_generation
        from .templates.loader import READY_GROUPS, load_template

        file = Path(path).expanduser()
        if not file.exists():
            raise ValueError(f"找不到文件：{file}")
        template = load_template(purpose)
        if template.group not in READY_GROUPS:
            raise ValueError(f"不支持的用途：{purpose}（先用 list_purposes 查看）")
        if self.llm_factory is None and not self.settings.llm.ready:
            raise ValueError("还没有设置大模型的 API Key：请在 CardMaker App 的「设置」里粘贴。")
        params = {"meaning_language": meaning_language or self.settings.meaning_language}
        if level:
            params["level"] = level
        options = GenerateOptions(pages=_pages(pages), max_cards=max_cards, ocr_langs=self.settings.ocr.langs,
                                  dpi=self.settings.ocr.dpi, book_title=title or None,
                                  concurrency=self.settings.concurrency, source=template.source,
                                  **self.settings.llm.billing())
        llm = self.llm_factory() if self.llm_factory else make_llm(self.settings.llm)
        dictionary = None if self.llm_factory else make_dictionary(self.settings)
        result = run_generation(file, template, params, llm, options, progress=progress, store=self.store,
                                dictionary=dictionary)
        return (f"任务编号：{result.task_id}\n" + result.summary_text()
                + "\n\n前几张卡片：\n" + _json([c.fields for c in result.cards[:5]])
                + "\n\n用 get_cards 看全部，用 import_to_anki 导入或 export_apkg 导出。")

    # ---------------------------------------------------------------- 历史
    def list_tasks(self, limit: int = 20, progress=None) -> str:
        from .storage import status_text
        from .templates.loader import READY_GROUPS, list_templates

        ready = {t.id for t in list_templates() if t.group in READY_GROUPS}
        tasks = [t for t in self.store.list_tasks(limit=200) if t.template_id in ready][:limit]
        return _json([{"任务编号": t.id, "时间": t.created_at[:16], "书名": t.book_title, "用途": t.template_id,
                       "卡片": t.cards, "token": t.total_tokens, "状态": status_text(t)} for t in tasks])

    def get_cards(self, task_id: int, limit: int = 100, progress=None) -> str:
        cards = self._cards(task_id)
        return _json([{"序号": i, "导入状态": c.anki_status or "未导入", **c.fields}
                      for i, c in enumerate(cards[:limit], 1)])

    # ---------------------------------------------------------------- Anki
    def export_apkg(self, task_id: int, path: str, directions: str = "both", progress=None) -> str:
        from .anki import export_apkg

        result, template = self._result(task_id)
        target = Path(path).expanduser()
        if target.suffix.lower() != ".apkg":
            target = target / f"{result.book_title}.apkg"
        cards = [c for c in result.cards if c.selected]
        export_apkg(cards, template.note_type, template.spec.tags, result.book_title,
                    self._options(result.book_title, "", directions), target)
        return f"已导出 {len(cards)} 张卡片：{target}\n双击这个文件就会导入 Anki。"

    def import_to_anki(self, task_id: int, deck: str = "", directions: str = "both", progress=None) -> str:
        from .anki import AnkiConnect, import_to_anki

        result, template = self._result(task_id)
        cards = [c for c in result.cards if c.selected]
        client = self.anki_factory() if self.anki_factory else AnkiConnect(self.settings.anki_url)
        options = self._options(result.book_title, deck, directions)
        report = import_to_anki(client, cards, template.note_type, template.spec.tags, result.book_title, options)
        for card, status in zip(cards, report.statuses):
            card.anki_status = status
        self.store.save_cards(task_id, result.cards)  # App 的历史记录里也能看到导入结果
        errors = "\n失败原因：" + "；".join(report.errors[:3]) if report.errors else ""
        return f"牌组「{report.deck}」：{report.summary()}{errors}"

    # ---------------------------------------------------------------- 工具函数
    def _result(self, task_id: int):
        from .pipeline.runner import load_result

        try:
            return load_result(self.store, int(task_id))
        except KeyError:
            raise ValueError(f"没有编号为 {task_id} 的任务（用 list_tasks 查看）") from None

    def _cards(self, task_id: int):
        return self._result(task_id)[0].cards

    @staticmethod
    def _options(book_title: str, deck: str, directions: str):
        from .anki import ImportOptions
        from .anki.exporter import sanitize_tag

        if directions not in ("both", "forward", "reverse"):
            raise ValueError("directions 只能是 both、forward 或 reverse")
        return ImportOptions(deck=deck or f"CardMaker::{sanitize_tag(book_title)}",
                             forward=directions != "reverse", reverse=directions != "forward")

    # ---------------------------------------------------------------- 工具清单
    def tools(self) -> list[Tool]:
        text = {"type": "string"}
        number = {"type": "integer"}
        pages = {"type": "string", "description": "页码，例如 3-5 或 1,4,7-9；不填就是全部"}
        task = {"type": "integer", "description": "任务编号（generate_cards 或 list_tasks 返回的）"}
        directions = {"type": "string", "enum": ["both", "forward", "reverse"],
                      "description": "卡片方向：both 两个方向，forward 外语→释义，reverse 释义→外语"}
        return [
            Tool("extract_text", "提取 PDF 或文本文件的文字：文字层优先，扫描页自动 OCR（中韩英）。不花 token。"
                 "适合让 AI 自己整理、翻译、总结 PDF 内容。",
                 {"path": {**text, "description": "文件的完整路径"}, "pages": pages,
                  "ocr": {"type": "string", "enum": ["auto", "always", "never"], "description": "auto：没有文字层的页才 OCR"}},
                 ("path",), self.extract_text),
            Tool("list_purposes", "列出能做哪些卡片（用途）和每种用途的参数，例如学习者水平。", {}, (), self.list_purposes),
            Tool("generate_cards", "用设置里的大模型把文件做成 Anki 卡片，结果存进 CardMaker 的历史记录。"
                 "会花 token：资料长时先用 pages 选几页试。释义会用词典核对。",
                 {"path": {**text, "description": "PDF 或文本文件的完整路径"},
                  "purpose": {**text, "description": "用途编号，见 list_purposes，例如 vocab_book、vocab_reading"},
                  "pages": pages, "max_cards": {**number, "description": "最多生成几张，0 表示不限"},
                  "level": {**text, "description": "学习者水平，例如 TOPIK 4级、HSK 5级、IELTS 6.5"},
                  "meaning_language": {**text, "description": "释义用什么语言，例如 中文、English"},
                  "title": {**text, "description": "书名（不填就用文件名）"}},
                 ("path", "purpose"), self.generate_cards),
            Tool("list_tasks", "列出最近做过的卡片任务（任务编号、书名、卡片数、token）。",
                 {"limit": {**number, "description": "最多列几条，默认 20"}}, (), self.list_tasks),
            Tool("get_cards", "查看某个任务的卡片内容（正面、释义、原句、例句等）。",
                 {"task_id": task, "limit": {**number, "description": "最多返回几张，默认 100"}},
                 ("task_id",), self.get_cards),
            Tool("export_apkg", "把某个任务的卡片导出成 .apkg 文件，双击就能导入 Anki（不需要打开 Anki）。",
                 {"task_id": task, "path": {**text, "description": ".apkg 文件路径或要保存到的文件夹"},
                  "directions": directions},
                 ("task_id", "path"), self.export_apkg),
            Tool("import_to_anki", "把某个任务的卡片导入 Anki（要先打开 Anki 并安装 AnkiConnect 插件）。"
                 "Anki 里已有的卡片会自动跳过。",
                 {"task_id": task, "deck": {**text, "description": "牌组名，不填就是 CardMaker::书名"},
                  "directions": directions},
                 ("task_id",), self.import_to_anki),
        ]


class Server:
    """MCP 服务器：处理 initialize、tools/list、tools/call、ping；其余请求回「不支持」。"""

    def __init__(self, tools: CardMakerTools | None = None, write: Callable[[dict], None] | None = None):
        self.tools = {tool.name: tool for tool in (tools or CardMakerTools()).tools()}
        self.write = write or (lambda message: None)

    def handle(self, message: dict) -> dict | None:
        """处理一条消息，返回要回给 AI 助手的响应；通知（没有 id）不用回。"""
        method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if msg_id is None:
            return None
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "cardmaker", "version": __version__}, "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [tool.schema() for tool in self.tools.values()]}
        elif method == "tools/call":
            result = self.call(params)
        else:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"不支持的方法：{method}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def call(self, params: dict) -> dict:
        tool = self.tools.get(params.get("name"))
        token = (params.get("_meta") or {}).get("progressToken")
        if tool is None:
            return self._text(f"没有这个工具：{params.get('name')}", error=True)
        count = 0

        def progress(event: ProgressEvent) -> None:  # 生成、OCR 比较慢：边做边告诉 AI 助手做到哪了
            nonlocal count
            count += 1
            if token is not None:
                self.write({"jsonrpc": "2.0", "method": "notifications/progress",
                            "params": {"progressToken": token, "progress": count, "message": event.message}})

        try:
            with contextlib.redirect_stdout(sys.stderr):  # 标准输出只能走协议消息
                return self._text(tool.run(**(params.get("arguments") or {}), progress=progress))
        except TypeError as exc:
            return self._text(f"参数不对：{exc}", error=True)
        except Exception as exc:  # 工具出错要告诉 AI，让它换个做法，而不是整个连接断掉
            return self._text(f"出错：{exc}", error=True)

    @staticmethod
    def _text(text: str, error: bool = False) -> dict:
        return {"content": [{"type": "text", "text": text}], "isError": error}


def serve(stdin=None, stdout=None, tools: CardMakerTools | None = None) -> None:
    """一行读一条 JSON-RPC 消息，处理完把响应写成一行。"""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    lock = threading.Lock()

    def write(message: dict) -> None:
        with lock:
            stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            stdout.flush()

    server = Server(tools, write)
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是合法的 JSON"}})
            continue
        response = server.handle(message)
        if response is not None:
            write(response)


def main() -> None:
    from .log import setup_logging

    setup_logging()  # 日志写文件和标准错误输出；标准输出只留给协议消息
    serve()


if __name__ == "__main__":
    main()
