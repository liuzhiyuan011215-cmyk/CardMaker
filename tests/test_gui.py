"""界面测试：在后台（offscreen）运行，不弹窗口；大模型用假的，不花 token。"""
import json
import os
from collections import Counter
from pathlib import Path

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
from cardmaker.templates.loader import list_groups, load_template  # noqa: E402
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


def test_table_shows_dictionary_check(app, template):
    checked, plain = make_card(template), make_card(template)
    checked.context["dict_check"] = {"status": "corrected", "source": "한국어기초사전", "original": "船",
                                     "sense": "배3 (명사) 1. 梨：배나무의 열매."}
    model = CardTableModel([checked, plain], template)
    col = len(model.columns) + 1
    assert model.headerData(col, Qt.Orientation.Horizontal) == "词典"
    assert model.data(model.index(0, col)) == "已按词典更正" and model.data(model.index(1, col)) == ""
    assert "原来的释义：船" in model.data(model.index(0, col), Qt.ItemDataRole.ToolTipRole)
    assert not model.flags(model.index(0, col)) & Qt.ItemFlag.ItemIsEditable
    assert CardTableModel([plain], template).columnCount() == col  # 没有核对结果时不显示这一列


def test_review_page_opens_cards_with_dictionary_results(app, template):
    """回归：卡片带词典核对结果时，检查页调列宽曾经越界崩溃（「词典」列不是模板字段）。"""
    from pathlib import Path

    from cardmaker.gui.review_page import ReviewPage
    from cardmaker.pipeline.result import RunResult

    card = make_card(template)
    card.context["dict_check"] = {"status": "corrected", "original": "船"}
    page = ReviewPage()
    page.set_result(RunResult(Path("书.pdf"), template.id, template.name, {}, "书", cards=[card]), template)
    assert page.model.show_dict and page.model.columnCount() == len(page.model.columns) + 2
    header = page.table.horizontalHeader()
    meaning = page.model.columns.index("meaning") + 1
    assert header.visualIndex(len(page.model.columns) + 1) == header.visualIndex(meaning) + 1  # 「词典」紧挨「释义」


def test_unverified_cards_are_grouped_for_review(app, template):
    from pathlib import Path

    from cardmaker.gui.review_page import ReviewPage
    from cardmaker.pipeline.result import RunResult

    good, held = make_card(template), make_card(template)
    good.context["dict_check"] = {"status": "verified"}
    held.context["dict_check"] = {"status": "not_found"}
    held.selected = False
    page = ReviewPage()
    page.set_result(RunResult(Path("书.pdf"), template.id, template.name, {}, "书", cards=[good, held]), template)
    assert not page.pending_bar.isHidden() and "1" in page.pending_label.text()
    page.manual_button.setChecked(True)
    assert page.proxy.rowCount() == 1  # 只看待确认的卡
    page.model.setData(page.model.index(1, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
    assert page.pending_bar.isHidden() and not page.manual_button.isChecked()  # 用户看过并勾选，就处理完了
    assert page.proxy.rowCount() == 2


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
    assert "확산되다" in preview_html(card, reverse=False).split("<hr")[0]
    assert "自：扩散" in preview_html(card, reverse=True).split("<hr")[0]
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
    assert [e for e in events if e.stage == "generate"][-1].cards == 1


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
    purpose = window.purpose_page
    assert [g.id for g in purpose.groups] == ["vocab_book", "vocab_reading"]  # 其他用途先隐藏
    assert len(purpose.tiles) == 2 and not purpose.next_button.isEnabled()

    purpose.select(purpose.groups[1])  # 第一步：选「单词 · 阅读材料」
    window.to_source()
    assert window.stack.currentIndex() == 1
    source = window.source_page
    assert not source.start_button.isEnabled() and not source.variant_row.isVisibleTo(source)
    source.set_file(pdf)  # 第二步：选资料
    assert source.start_button.isEnabled() and "共 3 页" in source.file_detail.text()
    template, params, options = source.collect()
    assert template.id == "vocab_reading"
    assert params["level"] == "TOPIK 4级" and params["ai_example"] is True and options.pages is None

    source.page_mode.setCurrentIndex(1)
    source.page_from.setValue(2)
    source.page_to.setValue(3)
    assert source.collect()[2].pages == [2, 3]
    assert "预计处理 2 页" in source.estimate.text()


def test_law_has_card_type_choice(app, make_pdf):
    window = MainWindow()
    law = next(g for g in list_groups() if g.id == "law")  # 第一步里隐藏了，但模板还在
    window.purpose_page.select(law)
    window.to_source()
    source = window.source_page
    assert source.variant_row.isVisibleTo(source)
    assert source.template().id == "law_cloze"
    source.variant_group.button(1).setChecked(True)  # 改成选择题
    assert source.template().id == "law_mcq"
    source.set_file(make_pdf([TEXT]))
    assert source.collect()[0].id == "law_mcq"


# ---------------------------------------------------------------------- 导入 Anki 窗口
from cardmaker.anki import AnkiConnect, AnkiUnavailable  # noqa: E402
from cardmaker.gui.anki_dialog import AnkiImportDialog  # noqa: E402
from test_anki import FakeAnki  # noqa: E402


def make_word_card(template, word):
    data = {"word": word, "kind": "word", "pos": "自", "meaning": "释义", "source_sentence": f"{word} 문장."}
    return Card(template.render_card_fields(data, {"source": ""}), data, "1")


def test_anki_dialog_imports_and_marks_cards(app, template):
    fake = FakeAnki(existing_fronts={"방치되다"})
    cards = [make_word_card(template, "확산되다"), make_word_card(template, "방치되다")]
    dialog = AnkiImportDialog(None, cards, template, "书", Settings(), client=AnkiConnect(transport=fake),
                              autostart=False)
    dialog.start_check(background=False)
    assert dialog.connected and dialog.import_button.isEnabled()
    options = dialog.options()
    assert options.forward and options.reverse and options.deck == "CardMaker::书"  # 默认两个方向都要

    dialog.start_import(background=False)
    assert [c.anki_status for c in cards] == ["已导入", "已存在"]
    assert "已跳过" in dialog.result_label.text()
    assert fake.added[0]["fields"]["CardReverse"] == "y"


def test_anki_dialog_when_anki_is_closed(app, template, tmp_path):
    def closed(url, payload, timeout):
        raise AnkiUnavailable("连不上 Anki")

    dialog = AnkiImportDialog(None, [make_word_card(template, "합의")], template, "书", Settings(),
                              client=AnkiConnect(transport=closed), autostart=False)
    dialog.start_check(background=False)
    assert not dialog.connected and not dialog.import_button.isEnabled()
    assert "AnkiConnect" in dialog.status.text() and dialog.retry.isVisibleTo(dialog)
    path = dialog.export_to(tmp_path / "书.apkg")  # 没开 Anki 也能导出 .apkg
    assert path.stat().st_size > 0


def test_table_shows_anki_status_after_import(app, template):
    card = make_word_card(template, "합의")
    model = CardTableModel([card], template)
    before = model.columnCount()
    card.anki_status = "已导入"
    model.refresh_status()
    assert model.columnCount() == before + 1
    assert model.headerData(before, Qt.Orientation.Horizontal) == "Anki"
    assert model.data(model.index(0, before)) == "已导入"
    assert not model.flags(model.index(0, before)) & Qt.ItemFlag.ItemIsEditable


# ---------------------------------------------------------------------- 历史记录与断点续传
from cardmaker.gui.history_dialog import HistoryDialog  # noqa: E402
from cardmaker.pipeline.runner import run_generation  # noqa: E402
from cardmaker.storage import TaskStore  # noqa: E402
from cardmaker.pipeline.chunking import Chunk  # noqa: E402
from test_resume import S1, S2, PageFakeLLM, reply  # noqa: E402


@pytest.fixture
def cancelled_task(tmp_path, make_pdf, template):
    """一个取消在第 1 页之后的任务：第 1 页有卡片，第 2 页没处理。"""
    import threading

    store = TaskStore(tmp_path / "gui.db")
    pdf = make_pdf([f"PAGE1 {S1}", f"PAGE2 {S2}"])
    cancel = threading.Event()
    llm = PageFakeLLM({"PAGE1": reply(("확산되다", S1)), "PAGE2": reply(("방치되다", S2))})
    result = run_generation(pdf, template, {}, llm, GenerateOptions(min_chars=5),
                            progress=lambda e: e.stage == "generate" and e.done == 1 and cancel.set(),
                            cancel=cancel, store=store)
    return store, result.task_id


def test_history_dialog_buttons(app, cancelled_task):
    store, task_id = cancelled_task
    dialog = HistoryDialog(None, store)
    assert dialog.table.rowCount() == 1 and "可继续" in dialog.table.item(0, 7).text()
    assert dialog.table.item(0, 6).text() == "—"  # 没填单价：不知道费用
    assert dialog.open_button.isEnabled() and dialog.resume_button.isEnabled()
    resumed = []
    dialog.resume_requested.connect(resumed.append)
    dialog._resume()
    assert resumed == [task_id]


def test_hidden_purposes_are_not_listed(app, cancelled_task, tmp_path):
    """第一步里隐藏的用途（法律等），历史记录和「继续生成」提醒里也不出现。"""
    store, task_id = cancelled_task
    store.create_task(Path("law.pdf"), "法律", "law_cloze", {}, {}, 1, [Chunk("1", "", "本文", "", "文字层")])
    assert len(store.unfinished_tasks()) == 2
    dialog = HistoryDialog(None, store)
    assert [t.id for t in dialog.tasks] == [task_id]
    window = MainWindow(store)
    assert "1/2 页" in window.purpose_page.resume_text.text()  # 提醒的是单词任务，不是更新的法律任务


def test_resume_banner_and_open_task(app, cancelled_task):
    store, task_id = cancelled_task
    window = MainWindow(store)
    assert window.purpose_page.resume_panel.isVisibleTo(window.purpose_page)
    assert "1/2 页" in window.purpose_page.resume_text.text()

    window.open_task(task_id)
    assert window.stack.currentIndex() == 3
    assert [c.fields["Front"] for c in window.review_page.result.cards] == ["확산되다"]


def test_review_edits_are_saved(app, cancelled_task):
    store, task_id = cancelled_task
    window = MainWindow(store)
    window.open_task(task_id)
    page = window.review_page
    page.model.setData(page.model.index(0, page.model.columns.index("meaning") + 1), "扩散，蔓延")
    page.save()  # 平时由定时器在修改后自动触发
    assert store.load_cards(task_id)[0].fields["Meaning"] == "扩散，蔓延"


def test_worker_resumes_task(app, cancelled_task):
    store, task_id = cancelled_task
    llm = PageFakeLLM({"PAGE1": reply(), "PAGE2": reply(("방치되다", S2))})
    worker = GenerateWorker.for_resume(task_id, Settings(), llm=llm, store=store)
    results = []
    worker.finished.connect(results.append)
    worker.run()
    assert llm.calls == ["PAGE2"]
    assert [c.fields["Front"] for c in results[0].cards] == ["확산되다", "방치되다"]
    assert not store.get_task(task_id).unfinished


def test_switching_purpose_does_not_leave_old_card_types(app):
    """回归测试：切换用途后，上一个用途的「卡片类型」按钮不能残留。"""
    window = MainWindow()
    groups = {g.id: g for g in list_groups()}
    source = window.source_page
    for gid in ("vocab_reading", "law", "notes", "code"):
        source.set_group(groups[gid])
        texts = [b.text() for b in source.variant_group.buttons()]
        assert texts == [v.variant for v in groups[gid].variants]
        radios = [source.variant_layout.itemAt(i).widget() for i in range(source.variant_layout.count())]
        assert len([w for w in radios if w is not None]) == 1 + len(texts)  # 「卡片类型」标签 + 按钮


# ---------------------------------------------------------------------- 费用统计
def test_settings_dialog_saves_prices(app):
    from cardmaker.config import load_settings
    from cardmaker.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    dialog.price_in.setValue(0.3)
    dialog.price_out.setValue(3)
    dialog.accept()
    assert (load_settings().llm.price_input_per_m, load_settings().llm.price_output_per_m) == (0.3, 3)
    dialog.platform.setCurrentIndex(dialog.platform.findData("ollama"))
    assert not dialog.price_in.isEnabled()  # 本地模型不花钱，不用填


def test_review_page_shows_cost_and_details(app, template):
    from pathlib import Path

    from cardmaker.gui.anki_dialog import audio_summary
    from cardmaker.gui.review_page import ReviewPage
    from cardmaker.llm.client import Usage
    from cardmaker.pipeline.result import RunResult

    result = RunResult(Path("书.pdf"), template.id, template.name, {}, "书", cards=[make_card(template)],
                       usage=Usage(1000, 500, 0, 2), check_usage=Usage(200, 100, 0, 1), elapsed=12,
                       options={"model": "qwen-test", "price_input_per_m": 0.3, "price_output_per_m": 3})
    page = ReviewPage()
    page.set_result(result, template)
    assert "费用约 0.0018 元（平均每张 1,500 token，约 0.0018 元）" in page.summary.text()
    details = page.stats_text()
    assert "其中词典核对：300 token，调用 1 次" in details and "平均每张卡：1,500 token，12.0 秒" in details

    result.options = {"model": "qwen3.5:2b", "local_model": True}
    page.set_result(result, template)
    assert "本地模型，不花钱" in page.summary.text()
    assert "新合成 2 条（共 9 字，按量计费），3 条用了缓存" in audio_summary(
        Counter(new=2, new_chars=9, cached=3), free=False)
