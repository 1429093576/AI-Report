# Daily AI Insight Engine

AI 科技新闻日报 Pipeline。项目从公开在线来源采集当天候选新闻，经过清洗、AI 相关性准入、Memory 去重、结构化抽取、校验、历史上下文检索、可视化和分析，最终生成中文 Markdown 日报、图表、审计报告和 per-run 运行快照。

默认运行口径：

- `mode.source: online`
- `mode.llm: llm`
- `report_timezone: Asia/Shanghai`
- `report_language: zh-CN`

默认 LLM 模式是严格真实模型模式，必须配置 `LLM_API_KEY`。离线 / mock / auto fallback 仍保留给测试或显式降级路径，但不是当前默认运行方式。

## GitHub 提交版说明

GitHub 提交版只保留代码、Prompt、Hook、Skill、测试、文档、样例数据和样例日报。`logs/`、`memory/` 保留为空目录，用于运行时生成审计日志和 Topic Memory；`state/`、`.env`、`__pycache__/`、`*.pyc` 等本地运行产物不随仓库提交。

## 作业要求对应位置

| 作业要求 | 对应位置 |
| --- | --- |
| 项目代码或脚本 | `src/`、`scripts/`、`config/pipeline.yaml` |
| 所有 Prompt | `prompts/` |
| 原始数据文件 | `data/raw/ai_news_raw.json` |
| 数据源说明、选择理由、数据特点 | `docs/system_design.md` 第 3 节、`config/pipeline.yaml` 的 `sources` 配置 |
| 结构化 Schema 与设计思路 | `src/schemas/`、`docs/system_design.md` 第 4 节、`docs/architecture.md` 第 4 节 |
| 结构化抽取结果 | `data/processed/ai_news_structured.json`、`data/processed/ai_news_validated.json` |
| AI 分析日报样例 | `outputs/daily_report.md` |
| 可视化结果 | `outputs/charts/topic_distribution.png`、`outputs/charts/importance_ranking.png` |
| 系统设计思路与整体架构 | `docs/architecture.md`、`docs/system_design.md` |
| AI 使用方式、Prompt 设计、错误处理 | `docs/system_design.md` 第 6 节、`prompts/`、`src/adapters/llm.py`、`src/pipeline/relevance.py`、`src/pipeline/extract.py`、`src/pipeline/analyze.py` |
| 核心流程说明 | 本 README 的 Pipeline 部分、`docs/architecture.md` 第 3-4 节 |
| Harness Engineering 实现 | `src/harness/`、`docs/architecture.md` 第 5 节 |
| Hook 资产 | `hooks/` |
| Skills 资产 | `skills/`，其中 `skills/ai_news_relevance/` 是 AI 科技相关新闻准入 Skill |
| Agent 开发约束 | `AGENTS.md` |
| 测试 | `tests/`，运行命令为 `python -m unittest discover -q` |

## 快速开始

安装依赖：

```bash
pip install -r requirements.txt
```

配置真实 LLM key：

```bash
cp .env.example .env
# then edit .env and set LLM_API_KEY
```

运行完整 Pipeline：

```bash
python -m src.main
```

指定报告日复跑：

```bash
python -m src.main --run-date 2026-05-30
```

基于历史快照 replay / resume：

```bash
python -m src.main --replay-run-id <run_id> --from raw
python -m src.main --replay-run-id <run_id> --from relevant
python -m src.main --replay-run-id <run_id> --from validated
```

运行测试：

```bash
python -m unittest discover -q
```

真实 LLM smoke test：

```bash
python scripts/smoke_llm.py
```

`scripts/smoke_llm.py` 会产生真实 API 调用和成本，只用于人工联调。

## 本地前端

项目已提供本地 Web 运行控制台，形态是 FastAPI + Jinja，而不是桌面软件或纯静态 HTML。

启动方式：

```bash
python -m src.web.main
```

默认访问地址：

```text
http://127.0.0.1:8000
```

V1 能力：

- 首页提供“开始今日运行”按钮。
- 点击后启动一次完整 fresh run。
- 页面显示每个步骤的等待、运行、成功或失败状态。
- 当前步骤会展示“正在做什么”的中文说明。
- 运行完成后展示日报、metrics 和主要产物入口。

V1 暂不做 replay / resume 按钮、配置编辑、多任务并发或取消任务。CLI 入口仍可继续使用：`python -m src.main`。

## Pipeline

主入口是 `src/main.py`，当前主链路：

```text
collect -> clean -> relevance -> memory_dedupe -> extract -> validate
  -> visualize -> analyze -> generate_report
```

其中：

- `clean` 会按 `run_date + report_timezone` 执行报告日硬过滤。
- `relevance` 是 AI 科技日报准入 Gate，只允许同日报且 AI 相关的条目进入下游。
- `memory_dedupe` 会在抽取前用历史 Memory 强过滤重复事件。
- `validate` 会做 schema、报告日和 evidence audit 校验。
- `analyze` 会读取 `historical_context` 和结构化 `memory_context`。
- `generate_report` 只组装报告，不做评分、趋势或 Memory 写入。
- fresh run 且报告生成成功后，Hook 才会写入 latest Memory。

## 关键输出

| 路径 | 说明 |
| --- | --- |
| `outputs/daily_report.md` | 中文日报 |
| `outputs/report_sections.json` | Analyzer 输出的结构化日报段落 |
| `outputs/charts/topic_distribution.png` | 主题分布图 |
| `outputs/charts/importance_ranking.png` | 关注度排行图 |
| `data/processed/ai_news_relevant.json` | 通过 AI 相关性准入的新闻 |
| `data/processed/ai_news_validated.json` | 校验通过的结构化新闻 |
| `logs/*.json`、`logs/run_trace.jsonl` | 运行后生成的审计日志与 trace，提交版默认不保留历史运行日志 |
| `memory/topic_index.json`、`memory/items/*.json` | 运行后生成的 Topic Memory，提交版默认不保留历史记忆 |
| `state/run_manifest.json`、`state/runs/<run_id>/` | 运行后生成的 run snapshot，提交版默认不保留本地快照 |

## 配置要点

运行配置位于 `config/pipeline.yaml`。

常用字段：

- `report_timezone`: 默认 `Asia/Shanghai`
- `report_language`: 默认 `zh-CN`
- `run_date`: 可选；未配置时按报告时区取当天日期
- `mode.source`: 默认 `online`
- `mode.llm`: 默认 `llm`
- `pipeline.llm_max_concurrency`: 当前为 `10`
- `pipeline.llm_business_retry_attempts`: 当前为 `1`
- `memory.memory_window_days`: 当前为 `7`

默认 online 数据源包括 RSS、Google News RSS、arXiv、GitHub Releases、Hacker News、量子位和 IT之家。需要凭证或策略未定的来源保持 `enabled: false`。

## 目录导航

```text
config/       运行配置
data/         raw、cleaned、relevant、structured、validated 中间数据
docs/         架构与系统设计说明
hooks/        pre/post/error hooks 与 Memory 生命周期
logs/         trace、metrics、validation、LLM audit、Memory audit
memory/       Topic Memory 运行时目录
outputs/      Markdown 日报、report sections、图表
prompts/      Extractor / Analyzer / Validator prompt 资产
scripts/      smoke test 与调试脚本
skills/       可复用决策 Skill 资产
src/          pipeline、schemas、adapters、harness
tests/        unittest 测试
```

## 文档分工

| 文件 | 作用 |
| --- | --- |
| `README.md` | 项目入口、快速开始、关键路径和导航 |
| `AGENTS.md` | Codex / Agent 开发约束与仓库边界 |
| `docs/architecture.md` | 模块边界、依赖方向、文件流和 Harness 架构 |
| `docs/system_design.md` | 当前实现细节、运行机制、Memory v2、snapshot 和 metrics |

## 当前验证状态

- 当前提交版测试：在 `github_version/` 下运行 `python -m unittest discover -q`，`214` 个测试通过。
- 样例日报报告日：`2026-05-31`
- 样例数据链路计数：`raw 80 -> cleaned 32 -> relevant 18 -> structured 18 -> validated 18`
- 样例输出：`outputs/daily_report.md` 与 `outputs/charts/*.png`

## 安全提醒

提交、分享、打包或截图前，检查以下位置，避免泄漏真实 API key：

- `.env`
- `config/pipeline.yaml`
- `logs/`
- `outputs/`
- 终端输出记录

Trace 和 metrics 不应记录完整 Prompt、完整 LLM Response 或 API key，只保留模型、token、耗时、cost、成功状态和短错误摘要。

## 当前限制

- 默认模式需要真实 LLM key 和可用 OpenAI-compatible HTTP 服务。
- Google News publisher URL 解析和原站正文抓取仍需增强。
- 中文媒体 HTML 正文补全仍需继续打磨。
- Extractor / Analyzer 长输入策略仍可继续增强，例如 token 分块、正文压缩、片段选择或 Map-Reduce。
- 当前输出为 Markdown 和 PNG，尚未支持 HTML、PDF 或 PPT。
- 当前 replay / resume 只支持 `raw`、`relevant`、`validated` 三个边界。
