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
    assert tr("❌ 导入失败：连不上本地模型，请先打开 Ollama。") == "❌ 가져오기 실패: 로컬 모델에 연결할 수 없습니다. 먼저 Ollama를 실행하세요."
    assert tr("✅ 已导入 3 张到「X」，2 张 Anki 里已经有了，已跳过。") == "✅ 「X」에 3장 가져옴, 2장은 Anki에 이미 있음, 건너뜀."
    assert tr("导入完成。\n🔊 已配上发音。") == "가져오기 완료.\n🔊 발음을 넣었습니다."
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
    assert "1단계: 어떤 카드를 만들까요?" in texts and "다음  →" in texts
    window.close()


def test_anki_cards_use_korean_labels_in_a_new_note_type(korean):
    from cardmaker.anki.exporter import ImportOptions, resolve_note_type

    vocab = resolve_note_type("vocab", ImportOptions(deck="d"))
    html = "".join(t.front + t.back for t in vocab.templates)
    assert vocab.name == "CardMaker 단어" and "📖 원문" in html and "原文" not in html
    assert [t.name for t in vocab.templates] == ["외국어→뜻", "뜻→외국어"]
