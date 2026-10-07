"""本地数据库（SQLite）：保存任务、每一页（分块）的处理结果和生成的卡片。

用途：
- 断点续传：每一页处理完立刻写入，程序崩溃、断网、取消之后，重新打开可以从中断处继续，
  已经完成的页不会再花 token；
- 历史记录：以前生成的卡片（包括手动修改和导入 Anki 的结果）随时可以再打开。

每次操作单独打开一个连接，所以后台线程和界面线程可以放心同时使用。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import app_home
from .llm.client import Usage
from .pipeline.chunking import Chunk
from .pipeline.result import Card

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    input_path TEXT NOT NULL,
    book_title TEXT NOT NULL,
    template_id TEXT NOT NULL,
    params_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    pages_selected INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,              -- running / done / incomplete / cancelled
    elapsed REAL NOT NULL DEFAULT 0,   -- 累计耗时（秒），续传时累加
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chunks (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    seq INTEGER NOT NULL,              -- 在书中的顺序
    page_label TEXT NOT NULL,
    chapter TEXT NOT NULL,
    method TEXT NOT NULL,
    text TEXT NOT NULL,
    context TEXT NOT NULL,
    status TEXT NOT NULL,              -- pending / ok / failed / skipped
    skip_reason TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    raw_items INTEGER NOT NULL DEFAULT 0,
    invalid INTEGER NOT NULL DEFAULT 0,
    items_json TEXT NOT NULL DEFAULT '[]',   -- 校验通过的模型输出（原文校验、去重在汇总时做）
    error TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, seq)
);
CREATE TABLE IF NOT EXISTS cards (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    seq INTEGER NOT NULL,
    page_label TEXT NOT NULL,
    chapter TEXT NOT NULL,
    grounding TEXT NOT NULL,
    selected INTEGER NOT NULL,
    anki_status TEXT NOT NULL,
    data_json TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
);
"""

PENDING, OK, FAILED, SKIPPED = "pending", "ok", "failed", "skipped"
RUNNING, DONE, INCOMPLETE, CANCELLED = "running", "done", "incomplete", "cancelled"
# 词典核对时调用模型的用量：不属于哪一页，记在任务上（后来加的列，旧数据库打开时自动补上）
CHECK_COLUMNS = ("check_prompt_tokens", "check_completion_tokens", "check_cached_tokens", "check_calls")


@dataclass
class ChunkRecord:
    seq: int
    chunk: Chunk
    status: str = PENDING
    attempts: int = 0
    raw_items: int = 0
    invalid: int = 0
    items: list[dict] = field(default_factory=list)
    error: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass
class TaskInfo:
    id: int
    created_at: str
    updated_at: str
    input_path: str
    book_title: str
    template_id: str
    params: dict
    options: dict
    pages_selected: int
    status: str
    elapsed: float
    error: str
    chunks_total: int = 0
    chunks_ok: int = 0
    chunks_failed: int = 0
    chunks_pending: int = 0
    cards: int = 0
    usage: Usage = field(default_factory=Usage)  # 全部用量：各页生成 + 词典核对
    check_usage: Usage = field(default_factory=Usage)  # 其中词典核对的部分

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens

    @property
    def unfinished(self) -> bool:
        """还有没处理完或失败的页，可以继续。"""
        return self.chunks_pending + self.chunks_failed > 0


def status_text(task: TaskInfo) -> str:
    if task.status == DONE:
        return "已完成"
    if task.status == CANCELLED:
        return "已取消（可继续）"
    if task.status == INCOMPLETE:
        return "有失败的页（可继续）" if task.unfinished else "已完成"
    return "中断了（可继续）" if task.unfinished else "已完成"  # running 却不在运行：上次程序被关掉或崩溃


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def default_db_path() -> Path:
    return app_home() / "cardmaker.db"


class TaskStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or default_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            for name in CHECK_COLUMNS:  # 只加列，不动原有数据
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")  # 允许一边写一边读
        return db

    # ------------------------------------------------------------------ 任务
    def create_task(self, input_path: Path, book_title: str, template_id: str, params: dict, options: dict,
                    pages_selected: int, chunks: list[Chunk]) -> int:
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO tasks (created_at, updated_at, input_path, book_title, template_id, params_json,"
                " options_json, pages_selected, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), now(), str(input_path), book_title, template_id, json.dumps(params, ensure_ascii=False),
                 json.dumps(options, ensure_ascii=False), pages_selected, RUNNING))
            task_id = cur.lastrowid
            db.executemany(
                "INSERT INTO chunks (task_id, seq, page_label, chapter, method, text, context, status, skip_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(task_id, seq, c.page_label, c.chapter, c.method, c.text, c.context,
                  SKIPPED if c.skip_reason else PENDING, c.skip_reason) for seq, c in enumerate(chunks)])
        return task_id

    def set_task_status(self, task_id: int, status: str, elapsed_delta: float = 0.0, error: str = "") -> None:
        with self._connect() as db:
            db.execute("UPDATE tasks SET status = ?, elapsed = elapsed + ?, error = ?, updated_at = ? WHERE id = ?",
                       (status, elapsed_delta, error, now(), task_id))

    _TASK_QUERY = """
        SELECT t.*,
          (SELECT COUNT(*) FROM chunks c WHERE c.task_id = t.id AND c.status != 'skipped') AS chunks_total,
          (SELECT COUNT(*) FROM chunks c WHERE c.task_id = t.id AND c.status = 'ok') AS chunks_ok,
          (SELECT COUNT(*) FROM chunks c WHERE c.task_id = t.id AND c.status = 'failed') AS chunks_failed,
          (SELECT COUNT(*) FROM chunks c WHERE c.task_id = t.id AND c.status = 'pending') AS chunks_pending,
          (SELECT COUNT(*) FROM cards k WHERE k.task_id = t.id) AS cards,
          (SELECT COALESCE(SUM(prompt_tokens), 0) FROM chunks c WHERE c.task_id = t.id) AS prompt_tokens,
          (SELECT COALESCE(SUM(completion_tokens), 0) FROM chunks c WHERE c.task_id = t.id) AS completion_tokens,
          (SELECT COALESCE(SUM(cached_tokens), 0) FROM chunks c WHERE c.task_id = t.id) AS cached_tokens,
          (SELECT COALESCE(SUM(calls), 0) FROM chunks c WHERE c.task_id = t.id) AS calls
        FROM tasks t
    """

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskInfo:
        check = Usage(*(row[name] for name in CHECK_COLUMNS))
        usage = Usage(row["prompt_tokens"], row["completion_tokens"], row["cached_tokens"], row["calls"])
        usage.add(check)
        return TaskInfo(
            id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"], input_path=row["input_path"],
            book_title=row["book_title"], template_id=row["template_id"], params=json.loads(row["params_json"]),
            options=json.loads(row["options_json"]), pages_selected=row["pages_selected"], status=row["status"],
            elapsed=row["elapsed"], error=row["error"], chunks_total=row["chunks_total"], chunks_ok=row["chunks_ok"],
            chunks_failed=row["chunks_failed"], chunks_pending=row["chunks_pending"], cards=row["cards"],
            usage=usage, check_usage=check)

    def get_task(self, task_id: int) -> TaskInfo:
        with self._connect() as db:
            row = db.execute(self._TASK_QUERY + " WHERE t.id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"没有编号为 {task_id} 的任务")
        return self._task_from_row(row)

    def list_tasks(self, limit: int = 100) -> list[TaskInfo]:
        with self._connect() as db:
            rows = db.execute(self._TASK_QUERY + " ORDER BY t.id DESC LIMIT ?", (limit,)).fetchall()
        return [self._task_from_row(r) for r in rows]

    def unfinished_tasks(self) -> list[TaskInfo]:
        return [t for t in self.list_tasks() if t.unfinished]

    def add_check_usage(self, task_id: int, usage: Usage) -> None:
        """记下词典核对用掉的 token（续传时再核对一次，就再累加，和耗时一样）。"""
        with self._connect() as db:
            db.execute(f"UPDATE tasks SET {', '.join(f'{name} = {name} + ?' for name in CHECK_COLUMNS)} WHERE id = ?",
                       (usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens, usage.calls, task_id))

    # ------------------------------------------------------------------ 分块
    def load_chunks(self, task_id: int) -> list[ChunkRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM chunks WHERE task_id = ? ORDER BY seq", (task_id,)).fetchall()
        return [ChunkRecord(
            seq=r["seq"],
            chunk=Chunk(r["page_label"], r["chapter"], r["text"], r["context"], r["method"], r["skip_reason"]),
            status=r["status"], attempts=r["attempts"], raw_items=r["raw_items"], invalid=r["invalid"],
            items=json.loads(r["items_json"]), error=r["error"],
            usage=Usage(r["prompt_tokens"], r["completion_tokens"], r["cached_tokens"], r["calls"]),
        ) for r in rows]

    def save_chunk(self, task_id: int, rec: ChunkRecord) -> None:
        """一页处理完立刻调用：这就是「断点」。"""
        with self._connect() as db:
            db.execute(
                "UPDATE chunks SET status = ?, skip_reason = ?, attempts = ?, raw_items = ?, invalid = ?,"
                " items_json = ?, error = ?, prompt_tokens = ?, completion_tokens = ?, cached_tokens = ?, calls = ?"
                " WHERE task_id = ? AND seq = ?",
                (rec.status, rec.chunk.skip_reason, rec.attempts, rec.raw_items, rec.invalid,
                 json.dumps(rec.items, ensure_ascii=False), rec.error, rec.usage.prompt_tokens,
                 rec.usage.completion_tokens, rec.usage.cached_tokens, rec.usage.calls, task_id, rec.seq))
            db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now(), task_id))

    # ------------------------------------------------------------------ 卡片
    def save_cards(self, task_id: int, cards: list[Card]) -> None:
        """保存卡片的当前状态（包括用户的修改、勾选、导入 Anki 的结果），整体替换。"""
        with self._connect() as db:
            db.execute("DELETE FROM cards WHERE task_id = ?", (task_id,))  # 只删本程序自己的记录
            db.executemany(
                "INSERT INTO cards (task_id, seq, page_label, chapter, grounding, selected, anki_status,"
                " data_json, fields_json, context_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(task_id, seq, c.page_label, c.chapter, c.grounding, int(c.selected), c.anki_status,
                  json.dumps(c.data, ensure_ascii=False), json.dumps(c.fields, ensure_ascii=False),
                  json.dumps(c.context, ensure_ascii=False)) for seq, c in enumerate(cards)])

    def load_cards(self, task_id: int) -> list[Card]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM cards WHERE task_id = ? ORDER BY seq", (task_id,)).fetchall()
        return [Card(fields=json.loads(r["fields_json"]), data=json.loads(r["data_json"]), page_label=r["page_label"],
                     chapter=r["chapter"], grounding=r["grounding"], context=json.loads(r["context_json"]),
                     selected=bool(r["selected"]), anki_status=r["anki_status"]) for r in rows]
