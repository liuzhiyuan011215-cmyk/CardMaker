"""查词典，用来核对大模型给单词写的释义（见 pipeline/meaning_check.py）。按单词的语言自动选：

- 韩语单词：国立国语院《韩国语基础词典》（한국어기초사전，krdict）开放 API。
  Key 免费申请：krdict.korean.go.kr → 오픈 API → 인증키 신청，只要填邮箱；每天最多查 5 万次。
  每个义项都带中文（或英文、日文）译词，可以直接和卡片上的释义比对。
  请求网址里带着 Key，所以出错时只报原因，不记录也不显示网址。
- 英语、中文（以及没填 Key 时的韩语）单词：维基词典（kaikki.org 整理好的 JSON），不用 Key、不用注册。
  义项释义是英语的，和卡片释义的比对交给模型。内容授权 CC BY-SA。
- 查过的词存在本地缓存（SQLite），同一个词只查一次。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import app_home
from .tts import detect_language

SEARCH_URL = "https://krdict.korean.go.kr/api/search"
VIEW_URL = "https://krdict.korean.go.kr/api/view"
SOURCE_NAME = "한국어기초사전"
WIKTIONARY_URL = "https://kaikki.org/dictionary/{lang}/meaning/{path}.jsonl"
WIKTIONARY_NAME = "Wiktionary" 
KOWIKT_NAME = "한국어 위키낱말사전"
KOWIKT_LANGS = {"en": "영어", "zh": "중국어"}
ECDICT_NAME = "ECDICT"
PANLEX_NAME = "PanLex"
DIRECT_SOURCES = {SOURCE_NAME, ECDICT_NAME, KOWIKT_NAME, PANLEX_NAME}
WIKTIONARY_LANGS = {"en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean"}  # 单词的语言 → kaikki 的目录
# 卡片的释义语言 → (接口的 trans_lang 编号, 返回结果里的语言名)
TRANS_LANGS = {"中文": (11, "중국어"), "英语": (1, "영어"), "日语": (2, "일본어")}
OWN_LANGUAGE = "韩语"  # 韩国语基础词典的释义本身就是韩语，不用译词
ERROR_HINTS = {
    "010": "今天的词典查询次数已用完（每天 5 万次），明天会自动恢复",
    "020": "词典 Key 不正确或还没生效，请在「设置」里检查",
    "021": "词典 Key 已被停用，请重新申请",
}


class DictError(Exception):
    pass


@dataclass
class Sense:
    order: int
    definition: str  # 韩语释义
    trans_word: str = ""  # 译词，例如「梨」
    trans_dfn: str = ""  # 译文释义
    example: str = ""  # 词典里这个义项的例句（韩国语基础词典要另查，见 KrdictClient.example）
    native: bool = False  # definition 本身就是用户要的释义语言，更正时直接用


@dataclass
class Entry:
    target_code: str
    word: str
    sup_no: int  # 同形词编号：배¹（肚子）、배²（船）、배³（梨）……0 表示没有同形词
    pos: str
    senses: list[Sense] = field(default_factory=list)
    link: str = ""
    source: str = SOURCE_NAME

    @property
    def label(self) -> str:
        return f"{self.word}{self.sup_no}" if self.sup_no else self.word


def supports(meaning_language: str) -> bool:
    return meaning_language in TRANS_LANGS or meaning_language == OWN_LANGUAGE


def _text(node: ET.Element | None, tag: str) -> str:
    return " ".join((node.findtext(tag) or "").split()) if node is not None else ""


def _parse_root(xml_text: str) -> ET.Element:
    """接口报错（例如 Key 不对）时抛 DictError。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DictError(f"词典返回的内容无法解析：{exc}") from None
    if root.tag == "error":
        code = _text(root, "error_code")
        raise DictError(ERROR_HINTS.get(code) or f"词典接口报错（{code}）：{_text(root, 'message')}")
    return root


def parse_view(xml_text: str) -> list[Entry]:
    """解析详情接口返回的 XML，只取例句。用例还有词组、对话，只有「문장」能直接当例句。"""
    senses = []
    for order, node in enumerate(_parse_root(xml_text).iter("sense_info"), 1):  # 详情接口没有义项编号，按出现顺序
        sentences = [_text(x, "example") for x in node.findall("example_info") if _text(x, "type") == "문장"]
        senses.append(Sense(order, _text(node, "definition"), example=sentences[0] if sentences else ""))
    return [Entry("", "", 0, "", senses)]


def parse_search(xml_text: str, lang_name: str) -> list[Entry]:
    """解析搜索接口返回的 XML。"""
    root = _parse_root(xml_text)
    entries = []
    for item in root.iter("item"):
        senses = []
        for node in item.findall("sense"):
            translations = node.findall("translation")
            # 只要请求的那种语言；接口没写语言名时就都要
            picked = [t for t in translations if _text(t, "trans_lang") == lang_name] or translations
            senses.append(Sense(
                order=int(_text(node, "sense_order") or 0),
                definition=_text(node, "definition"),
                trans_word="；".join(w for w in (_text(t, "trans_word") for t in picked) if w),
                trans_dfn=" ".join(d for d in (_text(t, "trans_dfn") for t in picked) if d)))
        sup = _text(item, "sup_no")
        entries.append(Entry(_text(item, "target_code"), _text(item, "word"), int(sup) if sup.isdigit() else 0,
                             _text(item, "pos"), senses, _text(item, "link")))
    return entries


def _open(request, timeout: float, attempts: int = 3) -> str:
    """网络偶尔超时不代表词典坏了：多试两次再算失败。HTTP 错误（404、Key 不对）重试也没用。"""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == attempts:
                raise
            time.sleep(attempt)


_pace_lock = threading.Lock()
_last_request = 0.0
MIN_INTERVAL = 0.5  # 连续查词后电脑曾连不上词典网站（手机流量能打开），疑似被限流，所以放慢一点


def _pace() -> None:
    global _last_request
    with _pace_lock:
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _fetch(params: dict, timeout: float, base: str = SEARCH_URL) -> str:
    _pace()
    url = f"{base}?{urllib.parse.urlencode(params)}"
    try:
        return _open(url, timeout)
    except urllib.error.HTTPError as exc:
        raise DictError(f"词典服务出错（HTTP {exc.code}）") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise DictError(f"连接不上韩国语基础词典（可能是被限流）,"
                        f"请换个网络（如手机热点）后重试:{reason}") from None
                        


def lookup_key(word: str) -> str:
    """卡片上的单词 → 查词典用的词：去掉括号里的注释，「A/B」只查 A。"""
    word = re.sub(r"[（(\[【].*?[）)\]】]", "", word or "")
    word = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])","",word)
    return word.split("/")[0].strip()


class _Cache:
    """查过的词存在本地 SQLite 里，重启后也在。"""

    def __init__(self, path: Path | None):
        path = path or app_home() / "dict_cache.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, entries TEXT NOT NULL)")

    def get(self, key: str) -> list[Entry] | None:
        with self._lock:
            row = self._db.execute("SELECT entries FROM cache WHERE key = ?", (key,)).fetchone()
        return [Entry(**{**e, "senses": [Sense(**s) for s in e["senses"]]}) for e in json.loads(row[0])] if row else None

    def put(self, key: str, entries: list[Entry]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)",
                             (key, json.dumps([asdict(e) for e in entries], ensure_ascii=False)))
            self._db.commit()


class KrdictClient:
    def __init__(self, api_key: str, cache_path: Path | None = None,
                 fetch: Callable[[dict, float], str] = _fetch, timeout: float = 10):
        if not api_key:
            raise DictError("没有设置词典 Key")
        self._key, self._fetch, self.timeout = api_key, fetch, timeout
        self._cache = _Cache(cache_path)
        self.requests = 0  # 本次实际请求接口的次数（其余来自缓存）

    def covers(self, word: str, meaning_language: str = "中文") -> bool:
        return detect_language(word) == "ko" and supports(meaning_language)

    def lookup(self, word: str, meaning_language: str = "中文") -> list[Entry]:
        """查一个词（精确匹配词条），返回所有同形词及其义项；词典里没有时返回空列表。"""
        word = lookup_key(word)
        if not word:
            return []
        own = meaning_language == OWN_LANGUAGE
        lang_code, lang_name = ("ko", "") if own else TRANS_LANGS[meaning_language]
        key = f"{lang_code}:{word}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        params = {"key": self._key, "q": word, "part": "word", "advanced": "y", "method": "exact", "num": 20,
                  **({"translated": "n"} if own else {"translated": "y", "trans_lang": lang_code})}
        # 只要同一个词：「-배」（词缀）、「배-」（词干）这类带连字符的词条不算；「^」是词条里的分写标记
        plain = lambda w: re.sub(r"[\^\s]", "", w)
        entries = [e for e in parse_search(self._fetch(params, self.timeout), lang_name)
                   if plain(e.word) == plain(word)]
        if own:
            for sense in (s for e in entries for s in e.senses):
                sense.trans_word, sense.trans_dfn, sense.native = "", "", True
        self.requests += 1
        self._cache.put(key, entries)
        return entries

    def example(self, word: str, target_code: str, order: int, meaning_language: str = "中文") -> str:
        """搜索结果不带例句，要用词条编号另查一次详情。"""
        key = f"view2:{target_code}"
        entries = self._cache.get(key)
        if entries is None:
            entries = parse_view(self._fetch({"key": self._key, "q": target_code, "method": "target_code"},
                                             self.timeout, VIEW_URL))
            self.requests += 1
            self._cache.put(key, entries)
        return next((s.example for e in entries for s in e.senses if s.order == order), "")


def _wikt_example(sense: dict) -> str:
    """只要词典自己写的短例句；带出处的是文学引文，又长又旧，不适合放在卡片上。"""
    for example in sense.get("examples") or []:
        text = " ".join((example.get("text") or "").split())
        if (text and not example.get("ref") and example.get("type") != "quote" and len(text) <= 160
                and not text[0].islower() and text[-1] in ".!?。！？"):  # 「alleviate his pain」这种词组不算例句
            return text
    return ""


def _is_real_sense(sense: dict) -> bool:
    """维基词典连常见拼错的词（recieve）也收，但标了 misspelling；这种不算词典里有这个词。"""
    tags = sense.get("tags") or []
    return bool(sense.get("glosses")) and "misspelling" not in tags and not ("alt-of" in tags and "obsolete" in tags)


def _fetch_url(url: str, timeout: float) -> str:
    """GET 一个网址；404（词典里没有这个词）返回空字符串。"""
    request = urllib.request.Request(url, headers={"User-Agent": "CardMaker/1.0 (graduation project)"})
    try:
        return _open(request, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ""
        raise DictError(f"维基词典出错（HTTP {exc.code}）") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DictError(f"连不上维基词典：{getattr(exc, 'reason', exc)}") from None


class WiktionaryClient:
    """维基词典：不用 Key，英语、中文、日语、韩语单词都能查；义项释义是英语的。"""

    def __init__(self, cache_path: Path | None = None, fetch: Callable[[str, float], str] = _fetch_url,
                 timeout: float = 10):
        self._cache, self._fetch, self.timeout = _Cache(cache_path), fetch, timeout
        self.requests = 0

    def covers(self, word: str, meaning_language: str = "中文") -> bool:
        return bool(lookup_key(word))

    def lookup(self, word: str, meaning_language: str = "中文") -> list[Entry]:
        """释义都是英语的，和释义语言无关，所以缓存只按单词。"""
        word = lookup_key(word)
        if not word:
            return []
        lang = WIKTIONARY_LANGS[detect_language(word)]
        key = f"wikt4:{lang}:{word}"  # 4：不要拼写错误的义项，旧缓存里有
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        entries = self._entries(lang, word)
        self._cache.put(key, entries)
        return entries

    def _entries(self, lang: str, word: str, follow: bool = True) -> list[Entry]:
        path = "/".join(urllib.parse.quote(part) for part in (word[0], word[:2], word))
        self.requests += 1
        entries = []
        for line in self._fetch(WIKTIONARY_URL.format(lang=lang, path=path), self.timeout).splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("pos") == "soft-redirect":  # 简体字词条只指向繁体字词条（缓解 → 緩解）
                if follow and item.get("redirects"):
                    entries += self._entries(lang, item["redirects"][0], follow=False)
                continue
            glossed = [s for s in item.get("senses") or [] if _is_real_sense(s)]
            if glossed:
                entries.append(Entry("", item.get("word") or word, 0, item.get("pos") or "",
                                     [Sense(n, "; ".join(s["glosses"]), example=_wikt_example(s))
                                      for n, s in enumerate(glossed, 1)],
                                     f"https://en.wiktionary.org/wiki/{urllib.parse.quote(word)}", WIKTIONARY_NAME))
        return entries

    def example(self, word: str, target_code: str, order: int, meaning_language: str = "中文") -> str:
        return ""  # 例句已经在 Sense.example 里，不用另查
class KoWiktionaryClient:
    """韩语版维基百科：英文、中文单词——韩语释义（英韩。中韩）。离线 SQLite."""
    def __init__(self,path:Path | None = None):
        path = path or app_home() / "kowiktionary.sqlite"
        if not path.exists():
            raise DictError(f"没有找到韩语维基词典:{path}")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(f"file:{path}?mode=ro",uri=True,check_same_thread=False)
        self.requests = 0
    def covers(self,word:str,meaning_language:str = "中文")-> bool:
        return meaning_language == OWN_LANGUAGE and detect_language(lookup_key(word)) in KOWIKT_LANGS

    def lookup(self , word:str,meaning_language: str = OWN_LANGUAGE)-> list[Entry]:
        word = lookup_key(word)
        return self._entries(detect_language(word),word) if word else []

    def _entries(self, lang : str,word:str,follow:bool = True) -> list[Entry]:
        with self._lock:
            rows = self._db.execute("SELECT json FROM kowikt WHERE lang = ? AND word = ?",(lang,word)).fetchall()
        entries = []
        for row in rows:
            item = json.loads(row[0])
            glossed = []
            for s in item.get("senses") or []:
                if s.get("glosses"):
                    glossed.append(s)
            if not glossed:
                if follow and item.get("redirects"):
                    entries += self._entries(lang,item["redirects"][0],follow=False)
                continue
            senses = []
            for n,sense in enumerate(glossed,1):
                gloss = "; ".join(sense["glosses"]).strip().rstrip(".")
                senses.append(Sense(n,gloss,trans_word=gloss))
            entries.append(Entry("", item.get("word") or word, 0, item.get("pos") or "",senses,
                                     f"https://ko.wiktionary.org/wiki/{urllib.parse.quote(word)}", WIKTIONARY_NAME))
        return entries

    def example(self,word:str,target_code:str,order:int,meaning_language:str = "中文") -> str:
        return""

class Dictionaries:
    """按单词的语言选词典：韩语单词有 Key 时查《韩国语基础词典》（有中英日译词，最准），其余都查维基词典。"""

    def __init__(self, krdict: KrdictClient | None = None, wiktionary: WiktionaryClient | None = None):
        self.krdict, self.wiktionary = krdict, wiktionary

    def _pick(self, word: str, meaning_language: str):
        if self.krdict and self.krdict.covers(word, meaning_language):
            return self.krdict
        return self.wiktionary

    def covers(self, word: str, meaning_language: str = "中文") -> bool:
        client = self._pick(word, meaning_language)
        return client is not None and client.covers(word, meaning_language)

    def lookup(self, word: str, meaning_language: str = "中文") -> list[Entry]:
        client = self._pick(word, meaning_language)
        entries = client.lookup(word, meaning_language)
        # 基础词典只收常用词，查不到再问维基词典，免得正确的高级词汇被当成错字
        if not entries and client is self.krdict and self.wiktionary:
            return self.wiktionary.lookup(word, meaning_language)
        return entries

    def example(self, word: str, target_code: str, order: int, meaning_language: str = "中文") -> str:
        return self._pick(word, meaning_language).example(word, target_code, order, meaning_language)


def make_dictionary(settings) -> Dictionaries | None:
    """按设置创建词典；关掉时返回 None（不核对，其余功能照常）。没填 Key 时韩语单词也查维基词典。"""
    conf = settings.dictionary
    if not conf.enabled:
        return None
    return Dictionaries(KrdictClient(conf.krdict_key) if conf.krdict_key else None, WiktionaryClient())
