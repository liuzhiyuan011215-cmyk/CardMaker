"""命令行入口。核心流程不依赖界面，可以单独运行和测试。

示例：
  cardmaker config set-key                       # 粘贴一次 API Key
  cardmaker extract book.pdf --pages 1-3         # 只看提取出的文字（含 OCR）
  cardmaker templates list                       # 查看内置模板
  cardmaker generate book.pdf -t vocab_reading --level "TOPIK 4级" --max-cards 20
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path


def parse_pages(spec: str | None) -> list[int] | None:
    """把 "1-3,5" 解析成 [1, 2, 3, 5]（从 1 开始的物理页码）。"""
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.update(range(int(start), int(end) + 1))
        elif part:
            pages.add(int(part))
    return sorted(pages)


def parse_params(pairs: list[str] | None) -> dict[str, str]:
    params = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"参数格式应为 key=value：{pair}")
        params[key.strip()] = value.strip()
    return params


# ---------------------------------------------------------------- config
from .config import clean_pasted_key  # noqa: E402（测试里通过 cli.clean_pasted_key 使用）


def cmd_config(args) -> int:
    from .config import LLM_PRESETS, config_path, load_settings, mask_key, save_settings

    settings = load_settings()
    if args.action == "path":
        print(config_path())
    elif args.action == "show":
        data = settings.model_dump()
        data["llm"]["api_key"] = mask_key(settings.llm.api_key)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"\n配置文件：{config_path()}")
    elif args.action == "set-key":
        raw = getpass.getpass("请粘贴 API Key，粘贴一次即可（为了保密，屏幕上不会显示任何字符），然后按回车：")
        key, note = clean_pasted_key(raw)
        if not key:
            print("没有输入内容，已取消。")
            return 1
        settings.llm.api_key = key
        path = save_settings(settings)
        print(f"已保存 {mask_key(key)}（{len(key)} 位）到 {path}")
        if note:
            print(note)
        if not 20 <= len(key) <= 80:
            print("提示：这个长度不太像 API Key，如果连接失败请重新设置。")
    elif args.action == "preset":
        if args.value not in LLM_PRESETS:
            print(f"可选预设：{', '.join(LLM_PRESETS)}")
            return 1
        settings.llm.apply_preset(args.value)
        save_settings(settings)
        print(f"已切换到 {args.value}：{settings.llm.base_url}  模型 {settings.llm.model}")
    elif args.action == "test":
        return check_connection_cmd(settings)
    return 0


def check_connection_cmd(settings) -> int:
    from .llm.client import check_connection, explain_error

    if not settings.llm.ready:
        print("还没有设置 API Key，请先运行：cardmaker config set-key")
        return 1
    try:
        print(check_connection(settings.llm))
    except Exception as exc:
        print(f"连接失败：{exc if type(exc).__name__ == 'FatalLLMError' else explain_error(exc)}")
        return 1
    return 0


# ---------------------------------------------------------------- extract
def cmd_extract(args) -> int:
    from .config import load_settings
    from .ingest.pdf import extract_pdf

    settings = load_settings()
    doc = extract_pdf(Path(args.pdf), pages=parse_pages(args.pages), ocr_mode=args.ocr,
                      ocr_langs=settings.ocr.langs, dpi=settings.ocr.dpi,
                      progress=lambda event: print(event.message, file=sys.stderr))
    lines = [f"《{doc.title}》 共 {doc.page_count} 页"]
    for page in doc.pages:
        chapter = f" · {page.chapter}" if page.chapter else ""
        lines.append(f"\n===== 第 {page.label} 页（{page.method}）{chapter} =====\n{page.text}")
    output = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(output)
    return 0


# ---------------------------------------------------------------- templates
def cmd_templates(args) -> int:
    from .templates.loader import list_templates, load_template

    if args.action == "list":
        for tpl in list_templates():
            print(f"{tpl.id:16} {tpl.name}  —  {tpl.description}")
    else:
        tpl = load_template(args.template_id)
        print(tpl.source_path.read_text(encoding="utf-8"))
    return 0


# ---------------------------------------------------------------- tasks / resume
def cmd_tasks(args) -> int:
    from .storage import TaskStore, status_text

    tasks = TaskStore().list_tasks(limit=args.limit)
    if not tasks:
        print("还没有任何任务。")
    for t in tasks:
        print(f"#{t.id:<4} {t.created_at}  《{t.book_title}》 {t.template_id}  "
              f"{t.chunks_ok}/{t.chunks_total} 页  {t.cards} 张  {t.total_tokens:,} token  {status_text(t)}")
    return 0


def cmd_resume(args) -> int:
    from .config import load_settings
    from .llm.client import make_llm
    from .pipeline.runner import resume_task
    from .storage import TaskStore

    settings = load_settings()
    if not settings.llm.ready:
        print("还没有设置 API Key，请先运行：cardmaker config set-key")
        return 1
    from .dictionary import make_dictionary

    result = resume_task(TaskStore(), args.task_id, make_llm(settings.llm),
                         progress=lambda event: print(event.message, file=sys.stderr),
                         dictionary=make_dictionary(settings))
    return finish_run(result, args)


# ---------------------------------------------------------------- generate
def cmd_generate(args) -> int:
    from .config import load_settings, mask_key
    from .export.preview import write_outputs
    from .llm.client import make_llm
    from .pipeline.runner import GenerateOptions, run_generation
    from .templates.loader import load_template

    settings = load_settings()
    if not settings.llm.ready:
        print("还没有设置 API Key，请先运行：cardmaker config set-key")
        return 1

    template = load_template(args.template)
    params = parse_params(args.set)
    if args.level:
        params["level"] = args.level
    params.setdefault("meaning_language", settings.meaning_language)

    options = GenerateOptions(pages=parse_pages(args.pages), max_cards=args.max_cards,
                              ocr_mode=args.ocr, ocr_langs=settings.ocr.langs, dpi=settings.ocr.dpi,
                              book_title=args.title, concurrency=args.concurrency or settings.concurrency,
                              source=template.source, notes_book=args.book)
    llm = make_llm(settings.llm)
    print(f"模型：{settings.llm.model}（{settings.llm.base_url}，Key {mask_key(settings.llm.api_key)}）",
          file=sys.stderr)

    from .dictionary import make_dictionary

    result = run_generation(Path(args.input), template, params, llm, options,
                            progress=lambda event: print(event.message, file=sys.stderr),
                            dictionary=make_dictionary(settings))
    return finish_run(result, args)


def finish_run(result, args) -> int:
    from .export.preview import write_outputs

    out_dir = write_outputs(result, Path(args.out))
    preview = (out_dir / "cards.html").resolve()
    print("\n" + result.summary_text())
    print(f"\n结果已保存到：{out_dir}\n卡片预览：{preview}")
    if not args.no_open:
        import webbrowser

        webbrowser.open(preview.as_uri())
    return 0 if result.cards else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cardmaker", description="把 PDF 或读书笔记自动转换成 Anki 卡片")
    parser.add_argument("-v", "--verbose", action="store_true", help="终端显示详细日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("config", help="查看或修改配置")
    p.add_argument("action", choices=["show", "path", "set-key", "preset", "test"])
    p.add_argument("value", nargs="?", help="preset 的名称：dashscope / deepseek")
    p.set_defaults(func=cmd_config)

    ocr_help = "auto：没有可用文字层时才 OCR；always：全部 OCR；never：不做 OCR"
    p = sub.add_parser("extract", help="从 PDF 提取文字（文字层优先，必要时 OCR）")
    p.add_argument("pdf")
    p.add_argument("--pages", help="页码范围，如 1-3,5")
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto", help=ocr_help)
    p.add_argument("-o", "--out", help="写入文本文件而不是打印")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("templates", help="查看领域模板")
    p.add_argument("action", choices=["list", "show"])
    p.add_argument("template_id", nargs="?")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("generate", help="生成卡片")
    p.add_argument("input", help="PDF 或 .txt 文件")
    p.add_argument("-t", "--template", required=True, help="模板 ID，见 cardmaker templates list")
    p.add_argument("--level", help="学习者水平，如 \"TOPIK 4级\"、\"雅思 6分\"")
    p.add_argument("--pages", help="页码范围，如 1-3,5")
    p.add_argument("--max-cards", type=int, default=0, help="本次最多生成多少张卡（0 表示不限）")
    p.add_argument("--title", help="书名（默认用 PDF 元数据或文件名）")
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto", help=ocr_help)
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="模板参数，可重复")
    p.add_argument("-o", "--out", default="output", help="输出目录（默认 output/）")
    p.add_argument("--concurrency", type=int, default=0, help="同时处理几页（默认用设置里的值）")
    p.add_argument("--book", help="读书笔记里有多本书时，只处理这一本")
    p.add_argument("--no-open", action="store_true", help="生成后不自动在浏览器打开预览页")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("tasks", help="查看历史任务（断点续传用）")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("resume", help="继续一个没做完的任务（只处理没完成或失败的页）")
    p.add_argument("task_id", type=int)
    p.add_argument("-o", "--out", default="output", help="输出目录（默认 output/）")
    p.add_argument("--no-open", action="store_true", help="完成后不自动在浏览器打开预览页")
    p.set_defaults(func=cmd_resume)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from .log import setup_logging

    setup_logging(verbose=args.verbose)
    return args.func(args) or 0
