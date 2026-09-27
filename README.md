# CardMaker：PDF / 读书笔记 → Anki 卡片

本科毕业设计。把 PDF（文字版或扫描版）和读书笔记自动整理成 Anki 卡片：
导入 → 提取文字（文字层优先，扫描页走 OCR）→ 按领域模板调用大模型生成卡片 → 校验 → 预览编辑 → 写入 Anki。

> 当前进度：从拖入 PDF 到卡片进入 Anki 的完整流程已打通（选文件 → 选用途 → 生成 → 检查编辑 → 导入 Anki）。

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

1. **选择文件**：把 PDF / 文本拖进窗口，或点「用示例文件试试」；程序会自动判断文字版还是扫描版
2. **选择用途**：选模板（阅读材料挑生词 / 单词书整本收录），选学习者水平、页码范围、最多生成多少张
3. **生成卡片**：同时处理多页（默认 5 页，可在设置里调），显示进度、已用时间、预计剩余时间，可随时取消
4. **检查并导入**：表格里勾选、直接编辑、删除、搜索；右侧实时预览卡片正反面；
   点「导入 Anki」选牌组和方向（外语→释义 / 释义→外语 / 两个都要）即可写入 Anki

导入 Anki 需要 Anki 已打开并安装 AnkiConnect 插件（工具 → 插件 → 获取插件，代码 `2055492159`）。
程序会自动创建牌组和「CardMaker 单词」笔记类型；Anki 里已有的卡片自动跳过；只新增，不修改或删除任何已有内容。
没开 Anki 时可以导出 `.apkg` 文件，双击即可导入。

**断点续传与历史记录**：每一页处理完都会立即存进本地数据库。程序崩溃、断网、Key 出错或手动取消后，
第一步页面会提示「上次的任务还没做完」，点「继续生成」只处理没完成的页，已完成的页不会重复花 token。
右上角「📜 历史」可以打开以前生成的卡片（包括当时的修改和导入 Anki 的结果）。

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

## 设计要点

- **OCR 分段双模型融合**：RapidOCR 的每个识别模型只认一种文字，中韩混排的单词书用任何单个模型都会丢字。
  程序先检测文字行，纯中文行直接整行识别；其他行按空白切成小段，每段同时交给中文和韩语模型，
  取置信度高的结果，连续的汉字段再合并识别。详见 `src/cardmaker/ingest/ocr/rapid_fusion.py`。
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
  ingest/               导入：pdf.py（文字层 + OCR 兜底）、ocr/（OCR 接口与融合实现）
  llm/                  OpenAI 兼容客户端、JSON 解析
  templates/            模板加载器 + builtin/*.yaml 内置模板
  pipeline/             分块、生成主流程、原文校验与去重、结果数据结构
  export/               结果保存与 HTML 预览
  anki/                 写入 Anki：AnkiConnect 客户端、笔记类型（卡片模板与样式）、导入与 .apkg 导出
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
5. 其他领域模板 + 读书笔记导入（RIDI、Kindle 等）
6. TTS 发音（百炼 TTS）
7. Token 与费用统计 + 设置页
8. 界面多语言（中 / 韩 / 英）
9. 打包成 macOS 应用
