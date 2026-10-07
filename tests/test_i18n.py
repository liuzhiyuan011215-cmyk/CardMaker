"""韩语界面：翻译表的句式、嵌套翻译，以及界面能用韩语打开。"""
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cardmaker import i18n
from cardmaker.i18n import tr
from cardmaker.i18n_ko import KO


@pytest.fixture(scope="module")
def app():
    from cardmaker.gui.app import create_app
    return create_app(["test"])


@pytest.fixture
def korean():
    i18n.set_language("ko")
    yield
    i18n.set_language("zh")


def test_chinese_is_untouched_by_default():
    assert tr("开始生成") == "开始生成" and tr("已生成 3 张卡片") == "已生成 3 张卡片"


def test_exact_patterns_nested_and_multiline(korean):
    assert tr("开始生成") == "생성 시작"
    assert tr("已生成 3 张卡片") == "카드 3장 생성됨"
    assert tr("已导入 3 张到「CardMaker::책」") == "「CardMaker::책」에 3장 가져옴"  # 换了顺序
    # 报错里套着报错，多行逐行翻，逗号连起来的分段翻
    assert tr("导入失败：连不上本地模型，请先打开 Ollama。") == "가져오기 실패: 로컬 모델에 연결할 수 없습니다. 먼저 Ollama를 실행하세요."
    assert tr("已导入 3 张到「X」，2 张 Anki 里已经有了，已跳过") == "「X」에 3장 가져옴, 2장은 Anki에 이미 있음, 건너뜀"
    assert tr("导入完成。\n已配上发音。") == "가져오기 완료.\n발음을 넣었습니다."
    assert tr("经济学，第二版") == "经济学，第二版"  # 用户的书名不动
    assert tr(None) is None and tr(42) == 42


def test_every_pattern_has_matching_placeholders():
    for key, value in KO.items():
        n = key.count("{}")
        assert len(re.findall(r"\{\d*\}", value)) == n, key
        assert key.strip("{} "), key  # 不能只有占位符（会无限套娃）


def test_main_window_opens_in_korean(app, korean):
    from PySide6.QtWidgets import QLabel, QPushButton

    from cardmaker.gui.main_window import MainWindow
    window = MainWindow()
    texts = [w.text() for w in window.findChildren(QLabel) + window.findChildren(QPushButton)]
    assert "1단계: 어떤 카드를 만들까요?" in texts and "다음" in texts
    window.close()


def test_anki_cards_use_korean_labels_in_a_new_note_type(korean):
    from cardmaker.anki.exporter import ImportOptions, resolve_note_type

    vocab = resolve_note_type("vocab", ImportOptions(deck="d"))
    html = "".join(t.front + t.back for t in vocab.templates)
    assert vocab.name == "CardMaker 단어" and "원문" in html and "原文" not in html
    assert [t.name for t in vocab.templates] == ["외국어→뜻", "뜻→외국어"]


def test_cost_lines_translate(korean):
    assert tr("费用约 0.0018 元（平均每张 1,500 token，约 0.0018 元）") == \
        "비용 약 0.0018위안 (카드 1장당 평균 토큰 1,500개, 약 0.0018위안)"
    assert tr("页面：共选 3 页，成功 2，失败 0，跳过 0，未处理 1（可继续）") == \
        "페이지: 3쪽 선택, 성공 2, 실패 0, 건너뜀 0, 미처리 1 (계속 가능)"
    assert tr("卡片：5 张（单词 4、短语/句型 1）；丢弃：原文中找不到 2，重复 1") == \
        "카드: 5장 (단어 4, 구/문형 1); 버림: 원문에 없음 2, 중복 1"
    assert tr("《책》 · 模板「单词 · 阅读材料」 · 水平 TOPIK 4级").startswith("《책》 · 템플릿 「단어 · 읽기 자료」 · 수준")
    assert tr("其中词典核对：300 token，调用 1 次") == "그중 사전 대조: 토큰 300개, 호출 1회"
