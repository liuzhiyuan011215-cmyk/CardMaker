"""界面测试：在后台（offscreen）运行，不弹窗口；大模型用假的，不花 token。"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from cardmaker.config import Settings  # noqa: E402
from cardmaker.gui.app import create_app  # noqa: E402
from cardmaker.gui.main_window import MainWindow  # noqa: E402
from cardmaker.gui.review_page import CardTableModel, preview_html  # noqa: E402
from cardmaker.gui.worker import GenerateWorker  # noqa: E402
from cardmaker.llm.client import FatalLLMError  # noqa: E402
from cardmaker.pipeline.result import Card  # noqa: E402
from cardmaker.pipeline.runner import GenerateOptions  # noqa: E402
from cardmaker.templates.loader import load_template  # noqa: E402
from conftest import FakeLLM  # noqa: E402

TEXT = "공유 경제가 빠르게 확산되고 있다. 차는 주차장에 방치되기 마련이다. " * 3


@pytest.fixture(scope="module")
def app():
    return create_app(["test"])


@pytest.fixture
def template():
    return load_template("vocab_reading")


def make_card(template):
    data = {"word": "확산되다", "surface": "확산되고", "kind": "word", "pos": "自", "meaning": "扩散",
            "source_sentence": "빠르게 확산되고 있다."}
    context = {"source": "《书》 · p.1"}
    return Card(template.render_card_fields(data, context), data, "1", context=context)


def test_table_edit_rerenders_card_fields(app, template):
    card = make_card(template)
    model = CardTableModel([card], template)
    assert "surface" not in model.columns  # 内部字段不显示
    assert "example" not in model.columns  # 所有卡片都为空的字段不显示

    model.setData(model.index(0, model.columns.index("meaning") + 1), "蔓延")
    assert card.fields["Meaning"] == "自：蔓延"

    kind_col = model.columns.index("kind") + 1
    assert model.data(model.index(0, kind_col)) == "单词"
    assert not model.flags(model.index(0, kind_col)) & Qt.ItemFlag.ItemIsEditable


def test_table_checkbox_and_delete(app, template):
    cards = [make_card(template), make_card(template)]
    model = CardTableModel(cards, template)
    model.setData(model.index(0, 0), Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
    assert model.selected_count() == 1
    model.set_all_selected(True)
    assert model.selected_count() == 2
    model.remove_rows({0, 1})
    assert model.rowCount() == 0


def test_preview_directions(template):
    card = make_card(template)
    assert "확산되다" in preview_html(card, reverse=False).split("<hr>")[0]
    assert "自：扩散" in preview_html(card, reverse=True).split("<hr>")[0]
    assert "<b>확산되고</b>" in preview_html(card, reverse=False)


def test_worker_generates_with_progress(app, make_pdf, template):
    pdf = make_pdf([TEXT])
    reply = json.dumps({"items": [{"word": "확산되다", "meaning": "扩散",
                                   "source_sentence": "공유 경제가 빠르게 확산되고 있다."}]}, ensure_ascii=False)
    worker = GenerateWorker(pdf, template, {}, GenerateOptions(), Settings(), llm=FakeLLM([reply]))
    events, results = [], []
    worker.progress.connect(events.append)
    worker.finished.connect(results.append)
    worker.run()  # 测试里直接同步运行
    assert len(results) == 1 and len(results[0].cards) == 1
    assert events[-1].stage == "generate" and events[-1].cards == 1


def test_worker_reports_bad_key(app, make_pdf, template):
    pdf = make_pdf([TEXT])
    worker = GenerateWorker(pdf, template, {}, GenerateOptions(), Settings(),
                            llm=FakeLLM([FatalLLMError("API Key 不正确或已失效，请在「设置」里重新粘贴。")]))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "API Key" in failures[0]


def test_wizard_navigation(app, make_pdf):
    pdf = make_pdf([TEXT, TEXT, TEXT])
    window = MainWindow()
    assert not window.import_page.next_button.isEnabled()
    window.import_page.set_file(pdf)
    assert window.import_page.next_button.isEnabled()
    assert "共 3 页" in window.import_page.file_detail.text()

    window.to_options()
    assert window.stack.currentIndex() == 1
    assert window.options_page.template.id == "vocab_reading"  # 最常用的排第一
    template, params, options = window.options_page.collect()
    assert params["level"] == "TOPIK 4级" and params["ai_example"] is True and options.pages is None

    window.options_page.page_mode.setCurrentIndex(1)
    window.options_page.page_from.setValue(2)
    window.options_page.page_to.setValue(3)
    assert window.options_page.collect()[2].pages == [2, 3]
    assert "预计处理 2 页" in window.options_page.estimate.text()
