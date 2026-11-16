"""韩语版维基词典整份数据（kaikki.org rawdata，jsonl.gz）→ SQLite，只留英语、中文词条。
用法：build_kowiktionary.py <raw-wiktextract-data.jsonl.gz> <输出 kowiktionary.sqlite>
每行原样存 JSON（和在线按词查到的格式一样），客户端照旧解析。"""
import gzip, json, sqlite3, sys

src, out = sys.argv[1:]
db = sqlite3.connect(out)
db.execute("DROP TABLE IF EXISTS kowikt")
db.execute("CREATE TABLE kowikt (lang TEXT NOT NULL, word TEXT NOT NULL, json TEXT NOT NULL)")
n = 0
with gzip.open(src, "rt", encoding="utf-8") as f:
    for line in f:
        e = json.loads(line)
        if e.get("lang_code") in ("en", "zh") and e.get("word"):
            db.execute("INSERT INTO kowikt VALUES (?, ?, ?)", (e["lang_code"], e["word"], line.strip()))
            n += 1
db.execute("CREATE INDEX idx_kowikt ON kowikt (lang, word)")
db.commit(); db.execute("VACUUM")
print("词条", n)
