"""把 ECDICT 的 ecdict.csv（github.com/skywind3000/ECDICT，MIT）转成程序用的 SQLite：只留单词和中文译文。
用法：python scripts/build_ecdict.py ecdict.csv [输出路径，默认 程序数据目录/ecdict.sqlite]"""
import csv
import sqlite3
import sys
from pathlib import Path

from cardmaker.config import app_home

source = Path(sys.argv[1])
target = Path(sys.argv[2]) if len(sys.argv) > 2 else app_home() / "ecdict.sqlite"
target.unlink(missing_ok=True)
csv.field_size_limit(10**8)
db = sqlite3.connect(target)
db.execute("CREATE TABLE ecdict (key TEXT NOT NULL, word TEXT NOT NULL, translation TEXT NOT NULL)")
with open(source, encoding="utf-8", newline="") as f:
    db.executemany("INSERT INTO ecdict VALUES (?, ?, ?)",
                   ((row["word"].lower(), row["word"], row["translation"]) for row in csv.DictReader(f)
                    if row["translation"].strip()))
db.execute("CREATE INDEX ecdict_key ON ecdict (key)")
db.commit()
print(target, db.execute("SELECT count(*) FROM ecdict").fetchone()[0], "条")
