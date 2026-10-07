import sqlite3
from pathlib import Path

from cardmaker.llm.client import Usage
from cardmaker.pipeline.chunking import Chunk
from cardmaker.pipeline.result import Card
from cardmaker.storage import OK, PENDING, SCHEMA, SKIPPED, TaskStore


def test_task_chunks_and_cards_roundtrip(tmp_path):
    store = TaskStore(tmp_path / "t.db")
    chunks = [Chunk("37", "1장", "본문", "", "文字层"), Chunk("38", "1장", "짧", "", "文字层", skip_reason="字数太少")]
    task_id = store.create_task(Path("book.pdf"), "책", "vocab_reading", {"level": "TOPIK 4级"},
                                {"max_cards": 0}, 2, chunks)
    records = store.load_chunks(task_id)
    assert [(r.chunk.page_label, r.status) for r in records] == [("37", PENDING), ("38", SKIPPED)]
    assert store.get_task(task_id).unfinished

    records[0].status, records[0].items, records[0].usage = OK, [{"word": "합의"}], Usage(10, 5, 2, 1)
    store.save_chunk(task_id, records[0])
    task = store.get_task(task_id)
    assert (task.chunks_total, task.chunks_ok, task.total_tokens) == (1, 1, 15)
    assert not task.unfinished and task.params == {"level": "TOPIK 4级"}
    assert store.load_chunks(task_id)[0].items == [{"word": "합의"}]

    card = Card({"Front": "합의"}, {"word": "합의"}, "37", "1장", "approx", context={"source": "《책》"},
                selected=False, anki_status="已存在")
    store.save_cards(task_id, [card])
    store.save_cards(task_id, [card])  # 重复保存是整体替换，不会越存越多
    loaded = store.load_cards(task_id)
    assert len(loaded) == 1 and loaded[0] == card
    assert [t.id for t in store.list_tasks()] == [task_id] and store.get_task(task_id).cards == 1


def test_default_store_lives_in_app_home(isolated_home):
    assert TaskStore().path == isolated_home / "cardmaker.db"


def test_old_database_gets_check_usage_columns(tmp_path):
    """旧版本的数据库没有记词典核对用量的列：打开时自动补上，原来的任务照常读。"""
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)  # 旧版本的表结构
    db.execute("INSERT INTO tasks (created_at, updated_at, input_path, book_title, template_id, params_json,"
               " options_json, status) VALUES ('t', 't', 'a.pdf', '책', 'vocab_reading', '{}', '{}', 'done')")
    db.commit()
    db.close()
    store = TaskStore(path)
    assert store.get_task(1).check_usage == Usage() and store.get_task(1).total_tokens == 0
    store.add_check_usage(1, Usage(100, 50, 0, 1))
    store.add_check_usage(1, Usage(10, 5, 0, 1))  # 续传时又核对一次：累加
    task = store.get_task(1)
    assert task.check_usage == Usage(110, 55, 0, 2) and task.total_tokens == 165
