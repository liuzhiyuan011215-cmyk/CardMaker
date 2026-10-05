"""词典客户端：韩国语基础词典开放 API、维基词典（假接口，不联网）。"""
import json
import urllib.parse

import pytest

from cardmaker.dictionary import (WIKTIONARY_NAME, Dictionaries, DictError, KrdictClient, WiktionaryClient,
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
            return not self.korean_only or (any("가" <= ch <= "힣" for ch in word) and meaning_language != "韩语")

        def lookup(self, word, meaning_language="中文"):
            return self.name

    both = Dictionaries(Fake("krdict", True), Fake("wiktionary", False))
    assert both.lookup("배") == "krdict"
    assert both.lookup("배", "韩语") == "wiktionary"  # 基础词典没有韩语→韩语
    assert both.lookup("bank") == "wiktionary" and both.lookup("缓解") == "wiktionary"
    assert Dictionaries(None, Fake("wiktionary", False)).lookup("배") == "wiktionary"  # 没填 Key 也能查
    assert not Dictionaries(None, None).covers("배")
