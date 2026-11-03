"""词典客户端：韩国语基础词典开放 API、维基词典（假接口，不联网）。"""
import json
import urllib.parse

import pytest

from cardmaker.dictionary import (WIKTIONARY_NAME, Dictionaries, DictError, KrdictClient, WiktionaryClient, _wikt_example,
                                 lookup_key, parse_search)

# 按开放 API 文档的结构写的返回示例：「배」有三个同形词
SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <title>한국어 기초사전 개발 지원(Open API) - 사전 검색</title>
  <total>4</total><start>1</start><num>20</num>
  <item>
    <target_code>17000</target_code><word>배</word><sup_no>1</sup_no><pos>명사</pos>
    <link>https://krdict.korean.go.kr/dicSearch/SearchView?ParaWordNo=17000</link>
    <sense><sense_order>1</sense_order>
      <definition><![CDATA[사람이나 동물의 몸에서 위장, 창자 등의 내장이 들어 있는 곳.]]></definition>
      <translation><trans_lang>중국어</trans_lang><trans_word>肚子</trans_word>
        <trans_dfn>人或动物身体中装有胃、肠等内脏的地方。</trans_dfn></translation>
    </sense>
  </item>
  <item>
    <target_code>17001</target_code><word>배</word><sup_no>2</sup_no><pos>명사</pos>
    <sense><sense_order>1</sense_order><definition>사람이나 짐 등을 싣고 물 위를 다니는 교통수단.</definition>
      <translation><trans_lang>중국어</trans_lang><trans_word>船</trans_word><trans_dfn>载人或货物在水上行驶的交通工具。</trans_dfn></translation>
    </sense>
  </item>
  <item>
    <target_code>17002</target_code><word>배</word><sup_no>3</sup_no><pos>명사</pos>
    <sense><sense_order>1</sense_order><definition>배나무의 열매.</definition>
      <translation><trans_lang>중국어</trans_lang><trans_word>梨</trans_word><trans_dfn>梨树的果实。</trans_dfn></translation>
    </sense>
  </item>
  <item>
    <target_code>74950</target_code><word>-배</word><sup_no>0</sup_no><pos>접사</pos>
    <sense><sense_order>1</sense_order><definition>‘무리를 이룬 사람’의 뜻을 더하는 접미사.</definition></sense>
  </item>
  <item>
    <target_code>17100</target_code><word>배꼽</word><sup_no>0</sup_no><pos>명사</pos>
    <sense><sense_order>1</sense_order><definition>배의 한가운데에 있는 자국.</definition>
      <translation><trans_lang>중국어</trans_lang><trans_word>肚脐</trans_word><trans_dfn>肚子正中间的痕迹。</trans_dfn></translation>
    </sense>
  </item>
</channel>"""

ERROR_XML = "<error><error_code>020</error_code><message>등록되지 않은 인증키입니다.</message></error>"


class FakeFetch:
    def __init__(self, text=SAMPLE):
        self.text, self.calls = text, []

    def __call__(self, params, timeout):
        self.calls.append(params)
        return self.text


def test_parse_search_reads_homographs_and_chinese_translations():
    entries = parse_search(SAMPLE, "중국어")
    assert [(e.label, e.senses[0].trans_word) for e in entries] == [
        ("배1", "肚子"), ("배2", "船"), ("배3", "梨"), ("-배", ""), ("배꼽", "肚脐")]
    assert entries[0].senses[0].definition.startswith("사람이나 동물의 몸에서")


def test_api_error_becomes_friendly_message():
    with pytest.raises(DictError, match="词典 Key 不正确"):
        parse_search(ERROR_XML, "중국어")


def test_lookup_is_exact_and_cached(tmp_path):
    fetch = FakeFetch()
    client = KrdictClient("secret-key", cache_path=tmp_path / "cache.sqlite", fetch=fetch)
    entries = client.lookup("배")
    assert [e.label for e in entries] == ["배1", "배2", "배3"]  # 「배꼽」「-배」（词缀）都不是这个词，去掉
    assert fetch.calls[0]["trans_lang"] == 11 and fetch.calls[0]["method"] == "exact"
    client.lookup("배")
    assert len(fetch.calls) == 1 and client.requests == 1  # 第二次走缓存
    reopened = KrdictClient("secret-key", cache_path=tmp_path / "cache.sqlite", fetch=fetch)
    assert [e.label for e in reopened.lookup("배")] == ["배1", "배2", "배3"]  # 重启后缓存还在
    assert len(fetch.calls) == 1


def test_errors_never_contain_the_key(tmp_path):
    client = KrdictClient("secret-key", cache_path=tmp_path / "c.sqlite", fetch=FakeFetch(ERROR_XML))
    with pytest.raises(DictError) as info:
        client.lookup("배")
    assert "secret-key" not in str(info.value)


def test_lookup_key_strips_notes():
    assert lookup_key("배(梨)") == "배"
    assert lookup_key("공유하다/공유되다") == "공유하다"


def test_wiktionary_follows_simplified_chinese_redirects_and_caches(tmp_path):
    pages = {
        "缓解": {"word": "缓解", "pos": "soft-redirect", "redirects": ["緩解"], "senses": [{"tags": ["no-gloss"]}]},
        "緩解": {"word": "緩解", "pos": "verb", "senses": [{"glosses": ["to alleviate; to ease"]}, {"tags": ["no-gloss"]}]},
    }
    urls = []

    def fetch(url, timeout):
        urls.append(url)
        word = next(w for w in pages if url.endswith(f"/{urllib.parse.quote(w)}.jsonl"))
        return json.dumps(pages[word], ensure_ascii=False) + "\n"

    client = WiktionaryClient(cache_path=tmp_path / "c.sqlite", fetch=fetch)
    entries = client.lookup("缓解", "韩语")
    assert [(e.word, e.pos, [s.definition for s in e.senses], e.source) for e in entries] == [
        ("緩解", "verb", ["to alleviate; to ease"], WIKTIONARY_NAME)]
    assert "/Chinese/meaning/" in urls[0] and len(urls) == 2  # 简体词条只是跳转，再查一次繁体
    client.lookup("缓解", "中文")
    assert len(urls) == 2  # 释义都是英语的，换释义语言也走缓存


def test_korean_words_use_krdict_only_when_it_can_answer():
    class Fake:
        def __init__(self, name, korean_only):
            self.name, self.korean_only = name, korean_only

        def covers(self, word, meaning_language="中文"):
            return not self.korean_only or (any("가" <= ch <= "힣" for ch in word) and meaning_language != "法语")

        def lookup(self, word, meaning_language="中文"):
            return self.name

    both = Dictionaries(Fake("krdict", True), Fake("wiktionary", False))
    assert both.lookup("배") == "krdict"
    assert both.lookup("배", "韩语") == "krdict"  # 基础词典的释义本身就是韩语
    assert both.lookup("배", "法语") == "wiktionary"  # 基础词典没有这种译文
    assert both.lookup("bank") == "wiktionary" and both.lookup("缓解") == "wiktionary"
    assert Dictionaries(None, Fake("wiktionary", False)).lookup("배") == "wiktionary"  # 没填 Key 也能查
    assert not Dictionaries(None, None).covers("배")


def test_parse_view_keeps_only_full_sentences():
    from cardmaker.dictionary import parse_view
    xml = """<channel><item><word_info><pos_info><comm_pattern_info>
      <sense_info><definition>뜻</definition>
        <example_info><type>구</type><example>학교에 가다</example></example_info>
        <example_info><type>문장</type><example>나는 학교에 다닌다.</example></example_info></sense_info>
      <sense_info><definition>뜻2</definition>
        <example_info><type>대화</type><example>가: 어디 가?</example></example_info></sense_info>
    </comm_pattern_info></pos_info></word_info></item></channel>"""
    senses = parse_view(xml)[0].senses
    assert [(s.order, s.example) for s in senses] == [(1, "나는 학교에 다닌다."), (2, "")]


def test_krdict_example_is_fetched_once_with_the_target_code(tmp_path):
    from cardmaker.dictionary import KrdictClient
    calls = []

    def fetch(params, timeout, base=None):
        calls.append(params)
        return """<channel><item><sense_info><example_info><type>문장</type>
                  <example>예문입니다.</example></example_info></sense_info></item></channel>"""

    client = KrdictClient("k", tmp_path / "c.sqlite", fetch=fetch)
    assert client.example("학교", "123", 1) == "예문입니다." == client.example("학교", "123", 1)
    assert len(calls) == 1 and calls[0]["q"] == "123" and calls[0]["method"] == "target_code"


def test_wiktionary_keeps_short_own_examples_not_quotations():
    from cardmaker.dictionary import _wikt_example
    assert _wikt_example({"examples": [{"text": "1611: long ago", "ref": "King James"}, {"text": "A mirror reflects light."}]}) == "A mirror reflects light."
    assert _wikt_example({"examples": [{"text": "x" * 200}]}) == "" and _wikt_example({}) == ""


def test_korean_meanings_use_krdict_definitions_without_translations(tmp_path):
    fetch = FakeFetch()
    client = KrdictClient("key", cache_path=tmp_path / "c.sqlite", fetch=fetch)
    senses = [s for e in client.lookup("배", "韩语") for s in e.senses]
    assert senses and all(s.native and s.definition and not s.trans_word and not s.trans_dfn for s in senses)
    assert fetch.calls[0]["translated"] == "n" and "trans_lang" not in fetch.calls[0]
    client.lookup("배", "韩语")
    client.lookup("배", "中文")
    assert len(fetch.calls) == 2  # 韩语和中文分开缓存，不会互相串
    assert not any(s.native for e in client.lookup("배", "中文") for s in e.senses)


def test_words_missing_from_krdict_fall_back_to_wiktionary():
    class Fake:
        def __init__(self, entries):
            self.entries = entries

        def covers(self, word, meaning_language="中文"):
            return True

        def lookup(self, word, meaning_language="中文"):
            return self.entries

    assert Dictionaries(Fake([]), Fake(["wiktionary"])).lookup("완화하다", "韩语") == ["wiktionary"]
    assert Dictionaries(Fake(["krdict"]), Fake(["wiktionary"])).lookup("배") == ["krdict"]
    assert Dictionaries(Fake([]), None).lookup("배") == []


def test_wiktionary_examples_must_be_full_sentences():
    fragments = [{"text": "alleviate his pain"}, {"text": "to intensify the heat or cold"}, {"text": "Curb your dog"}]
    assert _wikt_example({"examples": fragments}) == ""
    assert _wikt_example({"examples": [*fragments, {"text": "He felt a surge of excitement."}]}) == "He felt a surge of excitement."


def test_misspelling_senses_do_not_count_as_real_words(tmp_path):
    pages = {"recieve": {"word": "recieve", "pos": "verb", "senses": [{"glosses": ["Misspelling of receive."], "tags": ["alt-of", "misspelling"]}]},
             "loose": [{"word": "loose", "pos": "adj", "senses": [{"glosses": ["Not fixed tightly."]}]},
                       {"word": "loose", "pos": "verb", "senses": [{"glosses": ["Obsolete form of lose."], "tags": ["alt-of", "obsolete"]},
                                                                  {"glosses": ["Misspelling of lose."], "tags": ["alt-of", "misspelling"]}]}]}

    def fetch(url, timeout):
        word = next(w for w in pages if url.endswith(f"/{w}.jsonl"))
        rows = pages[word] if isinstance(pages[word], list) else [pages[word]]
        return "\n".join(json.dumps(r) for r in rows)

    client = WiktionaryClient(cache_path=tmp_path / "c.sqlite", fetch=fetch)
    assert client.lookup("recieve") == []
    assert [(e.pos, [s.definition for s in e.senses]) for e in client.lookup("loose")] == [("adj", ["Not fixed tightly."])]


def test_network_timeouts_are_retried_but_http_errors_are_not(monkeypatch):
    import urllib.error
    import urllib.request

    from cardmaker import dictionary

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"ok"

    attempts = []

    def flaky(request, timeout):
        attempts.append(request)
        if len(attempts) < 3:
            raise TimeoutError("timed out")
        return Reply()

    monkeypatch.setattr(dictionary.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    assert dictionary._fetch({"q": "배"}, 1) == "ok" and len(attempts) == 3

    attempts.clear()

    def down(request, timeout):
        attempts.append(request)
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(DictError):
        dictionary._fetch({"q": "배"}, 1)
    assert len(attempts) == 3

    attempts.clear()

    def bad_key(request, timeout):
        attempts.append(request)
        raise urllib.error.HTTPError("u", 500, "err", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", bad_key)
    with pytest.raises(DictError):
        dictionary._fetch({"q": "배"}, 1)
    assert len(attempts) == 1


def test_dictionary_requests_are_paced(monkeypatch):
    from cardmaker import dictionary

    clock, slept = [100.0], []
    monkeypatch.setattr(dictionary.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(dictionary.time, "sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    monkeypatch.setattr(dictionary, "_last_request", 0.0)
    dictionary._pace()
    dictionary._pace()
    assert slept == [dictionary.MIN_INTERVAL]  # 第一次不用等，紧接着的第二次要等
