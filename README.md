# CardMaker：PDF / 读书笔记 → Anki 卡片

本科毕业设计。把 PDF（文字版或扫描版）和读书笔记自动整理成 Anki 卡片：
导入 → 提取文字（文字层优先，扫描页走 OCR）→ 按领域模板调用大模型生成卡片 → 校验 → 预览编辑 → 写入 Anki。

> 当前进度：成品 demo。界面第一步先只开放单词的两种用途（单词书 / 教材、阅读材料里的生词），一键导入 Anki 并自动配发音。
> 读书笔记、法律、编程、人文、自定义的模板（填空卡 / 选择题）都还在，评估做完再放出来（改 `gui/purpose_page.py` 里的 `SHOWN`）。

## 环境要求

- macOS（Apple Silicon），Python 3.11 及以上
- 一个 OpenAI 兼容接口的大模型 API Key（默认阿里云百炼 `qwen3.8-flash`，也内置了 DeepSeek 预设）

## 安装并打开 App

```bash
cd anki-card-maker
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python scripts/make_mac_launcher.py   # 生成可双击打开的 CardMaker.app
```

之后双击项目文件夹里的 **CardMaker.app** 即可（也可以拖到 Dock）。第一次生成卡片时会提示粘贴 API Key，
以后在右上角「⚙ 设置」里更换、测试连接。首次识别扫描件时会自动下载 OCR 模型（约 30MB）。

使用流程：

1. **选择用途**：单词书 / 教材、阅读材料里的生词，点一下卡片直接进入下一步
2. **选择资料和选项**：拖入 PDF / 文本（或粘贴、用示例文件），选卡片类型（填空卡 / 选择题）、学习者水平、页码范围等
3. **生成卡片**：同时处理多页（默认 5 页），显示进度、已用时间、预计剩余时间，可随时取消
4. **检查并导入**：表格里勾选、直接编辑、删除、搜索；右侧实时预览卡片正反面；点「导入 Anki」选牌组、方向和是否配发音

检查页上方显示用了多少 token、平均每张卡多少 token；在「⚙ 设置」里填上模型单价（元 / 百万 token）就能算出费用，本地模型不花钱。词典核对调用模型的 token 也算在内；「详细统计」里有输入 / 输出 token、平均每张卡的用时和费用。导入 Anki 时会显示发音新合成了几条（多少字）、几条用了缓存。

全部模板如下（界面上目前只显示前两种）：

| 用途 | 卡片 | 说明 |
|---|---|---|
| 单词 · 阅读材料 | 单词卡 | 按学习者水平（TOPIK 几级 / 雅思几分）挑生词和短语，释义结合原句语境 |
| 单词 · 单词书 | 单词卡 | 支持扫描件；书上的释义和例句原样保留，没有例句的才让 AI 补 |
| 读书笔记 | 填空卡 / 选择题 | Kindle 的 My Clippings.txt 或粘贴的划线；背面附原文和书名章节 |
| 法律条文 | 填空卡 / 选择题 | 只根据原文出题，挖掉的答案必须是原文原词，标注条款号 |
| 编程技术 | 填空卡 / 选择题 | 代码保留缩进，关键 API 可以在代码里挖空 |
| 人文社科 | 填空卡 / 选择题 | 概念、人物、时间、事件、观点 |
| 自定义 | 填空卡 / 选择题 | 一句话说明想学什么；也可以在「查看 / 编辑模板」里改提示词 |

单词卡导入时可以自动配发音（阿里云百炼 `qwen-audio-3.1-tts-flash`，韩语、中文、英语按文字自动选音色，
同样的内容只合成一次）。选择题的选项由程序打乱，正确答案不会总在同一个位置。

界面有中文、韩语、英语三种，在「⚙ 设置」→「界面语言」里切换（重新打开后生效）；没选过就跟随系统语言。
导入 Anki 的卡片标签也跟着界面语言走。

配置（含 API Key）保存在 `~/Library/Application Support/CardMaker/config.yaml`，不在项目目录里，不会进 git。
日志在同目录的 `logs/cardmaker.log`。

## 命令行（开发与测试用）

核心逻辑与界面完全分离，命令行可以单独运行同样的流程：

```bash
.venv/bin/cardmaker config set-key        # 粘贴 API Key；config test 测试连接
.venv/bin/cardmaker extract samples/demo/vocab_book_scan.pdf          # 只看提取出的文字（含 OCR）
.venv/bin/cardmaker templates list                                    # 查看模板
.venv/bin/cardmaker generate samples/demo/korean_reading.pdf -t vocab_reading --level "TOPIK 4级"
.venv/bin/cardmaker generate samples/demo/vocab_book_scan.pdf -t vocab_book --max-cards 20
.venv/bin/cardmaker tasks                  # 查看历史任务
.venv/bin/cardmaker resume 3               # 继续 3 号任务（只处理没完成或失败的页）
```

结果保存在 `output/<文件名>-<模板>-<时间>/`：`cards.html`（预览网页）、`cards.json`（卡片数据）、`summary.txt`（用量统计）。

## 让 AI 助手直接操作（MCP 接口）

CardMaker 带一个 [MCP](https://modelcontextprotocol.io) 接口（`src/cardmaker/mcp_server.py`，只用标准库）。
Claude、Cursor 这类支持 MCP 的 AI 助手接上以后，直接对它说「把这本 PDF 第 3～5 页做成 TOPIK 4 级的单词卡，导入 Anki」就行。
在 AI 助手的 MCP 设置里加一条（路径换成自己的项目位置）：

```json
"cardmaker": {"command": "/path/to/anki-card-maker/.venv/bin/python", "args": ["-m", "cardmaker.mcp_server"]}
```

| 工具 | 作用 |
|---|---|
| `extract_text` | 提取 PDF / 文本的文字（文字层优先，扫描页自动 OCR），不花 token |
| `list_purposes` | 能做哪些卡片和可选参数（学习者水平等） |
| `generate_cards` | 用设置里的大模型生成卡片（花 token），存进历史记录 |
| `list_tasks` / `get_cards` | 查看历史任务和卡片内容 |
| `export_apkg` | 导出 .apkg 文件 |
| `import_to_anki` | 导入 Anki（要先打开 Anki 和 AnkiConnect） |

大模型、语音、词典都用 App「设置」里的配置，API Key 不会出现在返回内容里。

## 设计要点

- **OCR**：RapidOCR 检测文字行，识别用 [Qota-OCR](https://huggingface.co/atonlee/Qota-OCR)（Apache-2.0），
  一个模型就能读同一行里混排的韩文、中文、英文，见 `src/cardmaker/ingest/ocr/qota.py`。
  模型下载不到时退回**分段双模型融合**：行按空白切成小段，每段同时交给中文和韩语模型，取置信度高的结果，
  连续的汉字段再合并识别（`rapid_fusion.py`）。真扫描单词书上 Qota 的字错误率比融合低一半以上。
- **模板 = 提示词 + 输出结构 + 字段映射**：每个模板一个 YAML 文件（`src/cardmaker/templates/builtin/`），
  输出结构在运行时生成 pydantic 模型做校验，用户选项可以增减要生成的内容。
- **防编造**：模型给出的原句、书上例句必须能在原文中找到（允许 OCR 造成的细微差异），找不到的直接丢弃。
- **并发 + 失败隔离**：线程池同时处理多页；调用失败按指数退避重试（2、4、8 秒，最多 3 次），输出不合格会把错误
  告诉模型让它改；仍失败只标记这一页，不影响其他页。Key 错误等致命错误会立即停止，修好后续传。
- **断点续传**：SQLite 保存任务、每一页的处理结果和卡片；无论哪页先完成，最后都按书中顺序做原文校验、
  去重（保留首次出现）和数量截断，结果与逐页顺序处理完全一致。
- **核心与界面分离**：`cardmaker` 的核心包不依赖界面；`cardmaker/gui/` 只负责展示和交互，
  耗时操作在后台线程运行，界面不会卡住。

## 测试

```bash
.venv/bin/pytest -m "not slow"   # 快速测试，不加载 OCR 模型、不调用大模型
.venv/bin/pytest                 # 全部测试（含真实 OCR 识别）
```

## 目录结构

```
src/cardmaker/
  cli.py                命令行入口
  config.py / log.py    配置（含 API Key）与日志
  storage.py            SQLite 任务存储（断点续传、历史记录）
  ingest/               导入：pdf.py（文字层 + OCR 兜底）、ocr/（OCR 接口、Qota 识别、融合实现）、notes.py（读书笔记解析）
  llm/                  OpenAI 兼容客户端、JSON 解析
  templates/            模板加载器 + builtin/*.yaml 内置模板
  pipeline/             分块、生成主流程、原文校验与去重、结果数据结构
  export/               结果保存与 HTML 预览
  anki/                 写入 Anki：AnkiConnect 客户端、三种笔记类型（单词 / 填空 / 选择题）、导入与 .apkg 导出
  tts.py                发音：百炼语音合成 + 本地缓存
  i18n.py               界面翻译：中文原文当键，i18n_ko.py / i18n_en.py 是韩语、英语翻译表
  gui/                  桌面界面（PySide6）：四步向导、设置窗口、导入 Anki 窗口、后台任务
tests/                  单元测试（含界面测试，在后台运行不弹窗口）
scripts/                演示样本生成、macOS 启动器生成
samples/demo/           自己编写的演示样本（无版权问题）；samples/private/ 放真实书籍，不提交
```

## 开发计划

1. ✅ 项目骨架 + 命令行核心流程（PDF → 文字/OCR → 单词模板 → JSON 校验 → 输出卡片）
2. ✅ AnkiConnect 写入 + .apkg 导出
3. ✅ SQLite 任务存储 + 断点续传 + 并发与重试 + 历史记录
4. ✅ 桌面界面（PySide6）：四步向导（为了尽早演示，提前到第二阶段之前完成）
5. ✅ 其他领域模板（法律、编程、人文、自定义，填空卡 / 选择题）+ 读书笔记导入（Kindle、通用格式；RIDI 待样本）
6. ✅ TTS 发音（百炼 qwen-audio-3.1-tts-flash）
7. ✅ Token 与费用统计（含词典核对的 token、每张卡的平均用量、发音用量）+ 设置页填单价
   - ✅ 界面重做：磨砂玻璃外框 + shadcn/ui 风格的控件 + 统一的立体图标；卡片标签去掉 emoji，预览和 Anki 卡片一致
   - ✅ 大模型接口（MCP）：AI 助手可以直接提取文字、生成卡片、导入 Anki
8. ✅ 界面多语言（中 / 韩 / 英）
9. 打包成 macOS 应用
