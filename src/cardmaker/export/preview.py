"""保存生成结果：cards.json（第二阶段导入 Anki 用）、cards.html（浏览器预览）、summary.txt。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..pipeline.result import RunResult

_ENV = Environment(loader=FileSystemLoader(Path(__file__).parent), autoescape=True)


def cards_payload(result: RunResult) -> dict:
    return {
        "book_title": result.book_title,
        "template": result.template_id,
        "params": result.params,
        "cards": [{"page": c.page_label, "chapter": c.chapter, "grounding": c.grounding,
                   "fields": c.fields, "data": c.data} for c in result.cards],
    }


def write_outputs(result: RunResult, out_root: Path) -> Path:
    out_dir = out_root / f"{result.input_path.stem}-{result.template_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cards.json").write_text(json.dumps(cards_payload(result), ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    (out_dir / "summary.txt").write_text(result.summary_text() + "\n", encoding="utf-8")
    html = _ENV.get_template("preview.html").render(result=result, summary=result.summary_lines())
    (out_dir / "cards.html").write_text(html, encoding="utf-8")
    return out_dir
