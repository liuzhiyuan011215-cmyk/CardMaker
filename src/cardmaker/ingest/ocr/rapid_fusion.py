"""RapidOCR「分段双模型融合」识别：解决中韩混排（例如中韩单词书）。

为什么要融合：RapidOCR / PaddleOCR 的每个识别模型只认一种文字——中文模型认不出韩文，
韩语模型认不出汉字；macOS 自带的 Vision 也只能以一种语言为主。单词书上一行里常常
同时有韩语词条和中文释义，任何单个模型都会丢掉其中一种。

流程（每页）：
1. 检测模型找出文字行，按阅读顺序排好（支持双栏）；
2. 每行先整行交给各语言模型识别。中文模型很有把握、其他模型明显不行 → 纯中文行，直接采用；
3. 其余行按列投影找空白切成小段（韩语、英语按词切开），每段取置信度最高的模型结果；
4. 连续的汉字段合并成一段，用中文模型再整体识别一次——汉字之间没有空格，
   按空白切容易把「行」「川」这类左右结构的字切碎；
5. 拼回整行：韩语、英语词之间保留空格，汉字之间不加空格。

注意：RapidOCR 默认的文字方向分类器会把韩语行误判为倒置并翻转，导致韩语几乎全错，
所以这里完全不用方向分类器。检测框也要适当外扩，否则行首行尾的字（例如细细的「一」）
会被裁掉。
"""
from __future__ import annotations

import logging
import unicodedata

import numpy as np

from .base import OcrLine

log = logging.getLogger(__name__)

# 语言代码 → RapidOCR 的识别模型名（均为 PP-OCRv5 mobile 版）
_LANG_MODELS = {"zh": "CH", "ko": "KOREAN", "en": "EN"}

PURE_HAN_MIN_SCORE = 0.90  # 整行中文结果至少这么有把握，才当作纯中文行
OTHER_MAX_SCORE = 0.60  # 同时其他语言模型的置信度要低于这个值
MIN_SEGMENT_SCORE = 0.30  # 低于这个置信度的段当作噪点丢弃


class RapidFusionOCR:
    name = "rapidocr-fusion"

    def __init__(self, langs: list[str] | tuple[str, ...] = ("zh", "ko")):
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        unknown = [lang for lang in langs if lang not in _LANG_MODELS]
        if unknown:
            raise ValueError(f"不支持的 OCR 语言：{unknown}，可选 {list(_LANG_MODELS)}")
        quiet = {"Global.log_level": "warning"}
        self._detector = RapidOCR(params=quiet)
        self._recognizers = {}
        for lang in langs:
            engine = RapidOCR(params={
                **quiet,
                "Rec.lang_type": getattr(LangRec, _LANG_MODELS[lang]),
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            })
            self._recognizers[lang] = engine.text_rec
        self.langs = list(langs)

    # ------------------------------------------------------------------ 公共接口
    def recognize(self, image: np.ndarray) -> list[OcrLine]:
        rows = order_boxes(self._detect(image), image.shape[1])
        boxes = [box for row in rows for box in row]
        if not boxes:
            return []
        crops = [image[y0:y1, x0:x1] for x0, y0, x1, y1 in boxes]
        whole = self._recognize_all(crops)

        # 第 2 步：纯中文行直接采用整行结果，其余行切段
        results: list[list[tuple[str, float, str]]] = [[] for _ in boxes]
        seg_jobs: list[tuple[int, int, int]] = []  # (行号, 裁剪左边界, 裁剪右边界)
        for i, crop in enumerate(crops):
            if self._is_pure_han(whole, i):
                results[i] = [(*whole["zh"][i], "zh")]
                continue
            segments = split_segments(crop)
            if len(segments) <= 1:
                results[i] = [pick_best([(*whole[lang][i], lang) for lang in self.langs])]
                continue
            pad = max(4, crop.shape[0] // 5)
            seg_jobs.extend((i, x0, x1) for x0, x1 in segment_bounds(segments, crop.shape[1], pad))

        # 第 3 步：各段交给所有模型，取置信度最高的
        seg_crops = [crops[i][:, x0:x1] for i, x0, x1 in seg_jobs]
        seg_results = self._recognize_all(seg_crops) if seg_crops else {}
        chosen = [pick_best([(*seg_results[lang][k], lang) for lang in self.langs])
                  for k in range(len(seg_jobs))]

        # 第 4 步：连续的汉字段合并后用中文模型重新识别
        by_line: dict[int, list[int]] = {}
        for k, (i, _, _) in enumerate(seg_jobs):
            by_line.setdefault(i, []).append(k)
        merge_jobs: list[tuple[int, int, list[int]]] = []  # (行号, 在 results 中的位置, 段序号列表)
        for i, ks in by_line.items():
            for run in han_runs(ks, chosen):
                if len(run) == 1:
                    results[i].append(chosen[run[0]])
                else:
                    merge_jobs.append((i, len(results[i]), run))
                    results[i].append(("", 0.0, "zh"))  # 占位，下面填入
        if merge_jobs and "zh" in self._recognizers:
            merged_crops = [crops[i][:, seg_jobs[run[0]][1]:seg_jobs[run[-1]][2]] for i, _, run in merge_jobs]
            merged = self._recognize_batch(self._recognizers["zh"], merged_crops)
            for (i, pos, run), (text, score) in zip(merge_jobs, merged):
                # 合并后反而更差（少见）就退回逐段结果
                pieces = [chosen[k] for k in run]
                if score >= min(p[1] for p in pieces):
                    results[i][pos] = (text, score, "zh")
                else:
                    results[i][pos] = (join_segments([p[0] for p in pieces]),
                                       float(np.mean([p[1] for p in pieces])), "zh")

        # 第 5 步：按行拼接
        lines = []
        for row in rows:
            parts = []
            for box in row:
                i = boxes.index(box)
                parts.extend(p for p in results[i] if p[0].strip() and p[1] >= MIN_SEGMENT_SCORE)
            if not parts:
                continue
            x0 = min(b[0] for b in row); y0 = min(b[1] for b in row)
            x1 = max(b[2] for b in row); y1 = max(b[3] for b in row)
            lines.append(OcrLine(text=join_segments([p[0] for p in parts]), box=(x0, y0, x1, y1),
                                 score=float(np.mean([p[1] for p in parts])), langs=[p[2] for p in parts]))
        return lines

    # ------------------------------------------------------------------ 内部
    def _detect(self, image: np.ndarray) -> list[tuple[int, int, int, int]]:
        result = self._detector(image, use_rec=False, use_cls=False)
        if result.boxes is None:
            return []
        h, w = image.shape[:2]
        boxes = []
        for pts in result.boxes:
            x0, y0 = np.floor(pts.min(axis=0)).astype(int)
            x1, y1 = np.ceil(pts.max(axis=0)).astype(int)
            text_h = y1 - y0
            pad_x, pad_y = max(8, int(text_h * 0.45)), max(4, int(text_h * 0.25))
            boxes.append((int(max(0, x0 - pad_x)), int(max(0, y0 - pad_y)),
                          int(min(w, x1 + pad_x)), int(min(h, y1 + pad_y))))
        return boxes

    def _is_pure_han(self, whole: dict, i: int) -> bool:
        if "zh" not in whole:
            return False
        text, score = whole["zh"][i]
        others = [whole[lang][i][1] for lang in self.langs if lang != "zh"]
        return (score >= PURE_HAN_MIN_SCORE and has_han(text)
                and all(s < OTHER_MAX_SCORE for s in others))

    def _recognize_all(self, crops: list[np.ndarray]) -> dict[str, list[tuple[str, float]]]:
        return {lang: self._recognize_batch(rec, crops) for lang, rec in self._recognizers.items()}

    @staticmethod
    def _recognize_batch(recognizer, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        from rapidocr.ch_ppocr_rec import TextRecInput

        out = recognizer(TextRecInput(img=[np.ascontiguousarray(c) for c in crops]))
        txts = out.txts or ("",) * len(crops)
        return [(t or "", float(s)) for t, s in zip(txts, out.scores)]


# ---------------------------------------------------------------------- 版面工具函数
def order_boxes(boxes: list[tuple[int, int, int, int]], page_width: int) -> list[list[tuple]]:
    """把文字框整理成按阅读顺序排列的「行」。检测到明显的双栏时，先读左栏再读右栏。"""
    gutter = find_column_gutter(boxes, page_width)
    if gutter is not None:
        left = [b for b in boxes if b[2] <= gutter]
        right = [b for b in boxes if b[0] >= gutter]
        return group_rows(left) + group_rows(right)
    return group_rows(boxes)


def find_column_gutter(boxes, page_width: int) -> int | None:
    """在页面中部找一条没有任何文字框跨过的竖直空白带，找到则认为是双栏排版。"""
    if len(boxes) < 8:
        return None
    for x in range(int(page_width * 0.35), int(page_width * 0.65), max(1, page_width // 200)):
        if any(b[0] < x < b[2] for b in boxes):
            continue
        left = sum(1 for b in boxes if b[2] <= x)
        if left >= len(boxes) * 0.25 and (len(boxes) - left) >= len(boxes) * 0.25:
            return x
    return None


def group_rows(boxes) -> list[list[tuple]]:
    """纵向位置相近的框归为同一行，行内从左到右排序。"""
    rows: list[list[tuple]] = []
    for box in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        center, height = (box[1] + box[3]) / 2, box[3] - box[1]
        if rows:
            last = rows[-1]
            last_center = sum((b[1] + b[3]) / 2 for b in last) / len(last)
            if abs(center - last_center) < height * 0.4:
                last.append(box)
                continue
        rows.append([box])
    return [sorted(row, key=lambda b: b[0]) for row in rows]


def otsu_threshold(gray: np.ndarray) -> float:
    """大津法求二值化阈值，扫描件底色深浅不一时比固定阈值稳。"""
    hist, edges = np.histogram(gray, bins=64, range=(0, 256))
    total = hist.sum()
    if total == 0:
        return 128.0
    centers = (edges[:-1] + edges[1:]) / 2
    weight_bg = np.cumsum(hist)
    weight_fg = total - weight_bg
    mean_cum = np.cumsum(hist * centers)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean_cum[-1] * weight_bg - mean_cum * total) ** 2 / (weight_bg * weight_fg)
    return float(centers[int(np.argmax(np.nan_to_num(between)))])


def split_segments(line_img: np.ndarray) -> list[tuple[int, int]]:
    """按列投影把一行图像切成若干段（词或短语），返回每段的 [x0, x1)。"""
    if line_img.size == 0:
        return []
    gray = line_img.mean(axis=2) if line_img.ndim == 3 else line_img.astype(float)
    ink_rows = gray < otsu_threshold(gray)
    ink_per_col = ink_rows.sum(axis=0)
    rows_with_ink = np.flatnonzero(ink_rows.sum(axis=1) >= 2)
    text_h = (rows_with_ink[-1] - rows_with_ink[0] + 1) if rows_with_ink.size else gray.shape[0]
    has_ink = ink_per_col >= (2 if text_h >= 20 else 1)
    min_gap = max(5, round(text_h * 0.22))  # 比字内、字间空隙大，比词间空格小

    segments, start, gap = [], None, 0
    for x, ink in enumerate(has_ink):
        if ink:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                segments.append((start, x - gap + 1))
                start, gap = None, 0
    if start is not None:
        segments.append((start, int(np.flatnonzero(has_ink)[-1]) + 1))
    segments = [(a, b) for a, b in segments if b - a >= 2]
    return merge_narrow(segments, min_width=max(3, round(text_h * 0.45)))


def merge_narrow(segments: list[tuple[int, int]], min_width: int) -> list[tuple[int, int]]:
    """很窄的段（方括号、句号、单个数字等）单独识别容易出错，并入空隙更小的那一侧邻段。"""
    segs = list(segments)
    while len(segs) > 1:
        idx = next((i for i, (a, b) in enumerate(segs) if b - a < min_width), None)
        if idx is None:
            break
        left_gap = segs[idx][0] - segs[idx - 1][1] if idx > 0 else float("inf")
        right_gap = segs[idx + 1][0] - segs[idx][1] if idx < len(segs) - 1 else float("inf")
        lo, hi = (idx - 1, idx) if left_gap <= right_gap else (idx, idx + 1)
        segs[lo:hi + 1] = [(segs[lo][0], segs[hi][1])]
    return segs


def segment_bounds(segments: list[tuple[int, int]], width: int, pad: int) -> list[tuple[int, int]]:
    """每段的裁剪范围：左右各留 pad 像素的空白，但最多到和相邻段的中点，避免把邻段的字也裁进来。"""
    bounds = []
    for k, (a, b) in enumerate(segments):
        left = a - min(pad, (a - segments[k - 1][1]) // 2) if k > 0 else max(0, a - pad)
        right = b + min(pad, (segments[k + 1][0] - b) // 2) if k < len(segments) - 1 else min(width, b + pad)
        bounds.append((left, right))
    return bounds


_OWN_SCRIPT = {"zh": "CJK UNIFIED", "ko": "HANGUL", "en": "LATIN"}


def own_script_count(text: str, lang: str) -> int:
    """结果里属于该模型本语言文字的字符数（中文模型数汉字，韩语模型数韩文）。"""
    prefix = _OWN_SCRIPT[lang]
    return sum(1 for ch in text if unicodedata.name(ch, "").startswith(prefix))


def is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def pick_best(candidates: list[tuple[str, float, str]]) -> tuple[str, float, str]:
    """在各语言模型的结果中选一个。

    一般取置信度最高的；但模型遇到不认识的文字会直接跳过，置信度照样很高——
    例如韩语模型把「[自]」认成「[]」。所以：如果最高分的结果里没有它本语言的文字，
    而另一个结果有、并且前者只是后者的子序列，就说明前者漏认了，改用后者。
    """
    best = max(candidates, key=lambda c: c[1])
    squeeze = lambda t: "".join(t.split())
    for cand in candidates:
        if cand is best or cand[1] < 0.5:
            continue
        if (own_script_count(best[0], best[2]) == 0 and own_script_count(cand[0], cand[2]) > 0
                and is_subsequence(squeeze(best[0]), squeeze(cand[0]))):
            best = cand
    return best


def is_han(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith("CJK") or ch in "，。、；：？！（）《》“”「」【】"


def has_han(text: str) -> bool:
    return any(unicodedata.name(ch, "").startswith("CJK UNIFIED") for ch in text)


def han_runs(ks: list[int], chosen: list[tuple[str, float, str]]) -> list[list[int]]:
    """把一行中的段分组：连续的「中文模型胜出且含汉字」的段归为一组，其余各自一组。"""
    runs: list[list[int]] = []
    for k in ks:
        text, _, lang = chosen[k]
        is_han_seg = lang == "zh" and has_han(text)
        if is_han_seg and runs and runs[-1][-1] == k - 1:
            prev_text, _, prev_lang = chosen[runs[-1][-1]]
            if prev_lang == "zh" and has_han(prev_text):
                runs[-1].append(k)
                continue
        runs.append([k])
    return runs


def join_segments(texts: list[str]) -> str:
    """拼接各段：两个汉字段之间不加空格（中文本来就不分词），其余用空格分隔。"""
    out = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if out and not (is_han(out[-1]) and is_han(text[0])):
            out += " "
        out += text
    return out
