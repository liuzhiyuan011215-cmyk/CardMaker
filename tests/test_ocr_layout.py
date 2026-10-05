"""OCR 的版面与融合逻辑（不加载模型，毫秒级）。"""
import numpy as np

from cardmaker.ingest.ocr.rapid_fusion import (find_column_gutter, group_rows, han_runs, join_segments,
                                               order_boxes, pick_best, split_segments)


def _line_image(blocks, width=300, height=40):
    """白底图上画黑色矩形，模拟一行里的几个词。blocks = [(x0, x1), ...]"""
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for x0, x1 in blocks:
        img[10:30, x0:x1] = 0
    return img


def test_split_segments_by_word_gaps():
    # 词间空白 20px（大于阈值）→ 切开；字间 2px（小于阈值）→ 不切
    img = _line_image([(10, 50), (52, 90), (110, 160), (180, 220)])
    assert split_segments(img) == [(10, 90), (110, 160), (180, 220)]


def test_split_segments_blank_line():
    assert split_segments(np.full((40, 100, 3), 255, dtype=np.uint8)) == []


def test_group_rows_orders_top_to_bottom_left_to_right():
    boxes = [(300, 100, 400, 130), (10, 102, 200, 128), (10, 10, 200, 40)]
    rows = group_rows(boxes)
    assert rows == [[(10, 10, 200, 40)], [(10, 102, 200, 128), (300, 100, 400, 130)]]


def test_two_column_layout_reads_left_column_first():
    left = [(50, y, 450, y + 30) for y in range(100, 600, 50)]
    right = [(550, y, 950, y + 30) for y in range(100, 600, 50)]
    assert find_column_gutter(left + right, 1000) is not None
    rows = order_boxes(right + left, 1000)
    flat = [b for row in rows for b in row]
    assert flat[: len(left)] == left  # 先读完左栏


def test_single_column_when_lines_cross_middle():
    lines = [(50, y, 950, y + 30) for y in range(100, 600, 50)]
    assert find_column_gutter(lines, 1000) is None


def test_join_segments_spacing():
    assert join_segments(["확산되다", "[自]", "扩散，", "蔓延"]) == "확산되다 [自] 扩散，蔓延"
    assert join_segments(["The", "committee"]) == "The committee"


def test_pick_best_prefers_higher_score():
    assert pick_best([("학교", 0.95, "ko"), ("学校", 0.60, "zh")])[2] == "ko"


def test_pick_best_detects_dropped_characters():
    # 韩语模型不认识汉字，把「[自]」认成「[]」且置信度很高，应改用中文模型的结果
    assert pick_best([("[]", 0.97, "ko"), ("[自]", 0.90, "zh")]) == ("[自]", 0.90, "zh")


def test_pick_best_detects_dropped_characters_across_full_and_half_width():
    # 韩语模型把「（恒大地产集」认成半角的「(」：全角半角视为相同，仍判定为漏字
    assert pick_best([("(", 0.99, "ko"), ("（恒大地产集", 0.90, "zh")])[2] == "zh"


def test_pick_best_uses_chinese_model_for_digits_and_punctuation():
    # 实测（2026-10-05）：韩语模型置信度更高，却把括号和小数点认错了
    assert pick_best([("[2024] 56", 0.98, "ko"), ("〔2024〕56号", 0.90, "zh")]) == ("〔2024〕56号", 0.90, "zh")
    assert pick_best([("1 1 6", 0.95, "ko"), ("1.16", 0.85, "zh")]) == ("1.16", 0.85, "zh")
    # 中文模型自己也没把握时，不换
    assert pick_best([("123", 0.95, "ko"), ("l23", 0.30, "zh")])[2] == "ko"


def test_pick_best_keeps_korean_model_for_english_words():
    # 有字母就不算「只有数字标点」：英文仍交给韩语模型（它读英文带空格，更准）
    assert pick_best([("The committee", 0.97, "ko"), ("Thecommittee", 0.93, "zh")])[2] == "ko"


def test_han_runs_groups_consecutive_chinese_segments():
    chosen = [("확산되다", 0.9, "ko"), ("[自]", 0.9, "zh"), ("扩散，", 0.9, "zh"), ("蔓延", 0.9, "zh"), ("word", 0.9, "zh")]
    assert han_runs([0, 1, 2, 3, 4], chosen) == [[0], [1, 2, 3], [4]]


def test_han_runs_absorb_digits_and_punctuation():
    # 「罚没1.16亿元」被切成几段时，数字和小数点要和汉字一起交给中文模型重认
    chosen = [("罚没", 0.9, "zh"), ("1", 0.9, "zh"), ("16", 0.9, "ko"), ("亿元、", 0.9, "zh"), ("감사", 0.9, "ko")]
    assert han_runs([0, 1, 2, 3, 4], chosen) == [[0, 1, 2, 3], [4]]
