"""PanLex → 软件用的韩语兜底词库 panlex_ko.sqlite：英语（eng-000）和中文（cmn-000）单词 → 韩语译词。
只留程序要用的列，韩语译词只要两个音节以上的纯韩文（去掉单字汉字音和 $1$ 这类杂质）。
用法：build_panlex_ko.py <kor.tsv> <eng.tsv> <cmn.tsv> <输出.sqlite>"""
import re, sqlite3, sys
from collections import Counter, defaultdict

kor_tsv, eng_tsv, cmn_tsv, out = sys.argv[1:]


def rows(path, uid):
    with open(path, encoding="utf-8") as f:
        assert f.readline().rstrip("\r\n").split("\t") == ["id", "langvar", "txt", "txt_degr", "meaning", "langvar_uid"]
        for line in f:
            p = line.rstrip("\r\n").split("\t")
            if len(p) == 6 and p[5] == uid and p[2]:
                yield p[2], p[4]


meaning_kor, kor_total = defaultdict(set), Counter()
for txt, m in rows(kor_tsv, "kor-000"):
    meaning_kor[m].add(txt)
    kor_total[txt] += 1
clean = lambda k: re.fullmatch(r"[가-힣 ]+", k) and len(k.replace(" ", "")) >= 2

db = sqlite3.connect(out)
db.execute("DROP TABLE IF EXISTS panlex_pairs")
db.execute("CREATE TABLE panlex_pairs (key TEXT NOT NULL, kor TEXT NOT NULL, shared_meanings INTEGER NOT NULL, "
           "kor_meanings INTEGER NOT NULL, PRIMARY KEY (key, kor))")
for path, uid in ((eng_tsv, "eng-000"), (cmn_tsv, "cmn-000")):
    shared = Counter()
    for txt, m in rows(path, uid):
        for k in meaning_kor.get(m, ()):
            if clean(k):
                shared[(txt.lower(), k)] += 1
    db.executemany("INSERT OR REPLACE INTO panlex_pairs VALUES (?, ?, ?, ?)",
                   ((w, k, n, kor_total[k]) for (w, k), n in shared.items()))
    print(uid, "译词对", len(shared), "词数", len({w for w, _ in shared}))
db.commit(); db.execute("VACUUM"); db.close()
