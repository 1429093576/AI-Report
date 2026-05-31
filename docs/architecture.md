# Daily AI Insight Engine 架构说明

## 1. 当前架构摘要

本项目是一个 Python MVP，用文件流和轻量 Harness 组织 AI 新闻日报生产。当前主链路是：

```text
collect -> clean -> relevance -> memory_dedupe -> extract -> validate
  -> pre_analyze memory context -> visualize -> analyze -> generate_report
  -> post_validate memory write
```

系统只允许两类新闻进入日报下游：

- `published_at` 在 `run_date` 对应报告日内，报告时区由顶层 `report_timezone` 决定，默认 `Asia/Shanghai`。
- 通过 `relevance` AI 科技日报准入，阈值为 `is_ai_related = true` 且 `relevance_score >= 70`。

核心设计原则：

- 业务步骤放在 `src/pipeline/`。
- 编排、Hook、Trace、Checkpoint、Run snapshot、Memory、Metrics、Skill 执行放在 `src/harness/` 或 `hooks/`。
- 外部系统通过 `src/adapters/` 封装，业务模块不直接调用新闻 API、网页请求细节或 LLM 端点。
- 数据合同集中在 `src/schemas/`，所有进入下游的数据都必须经过 Pydantic 校验。
- latest 文件路径仍是默认集成面，per-run snapshot 负责审计、回放和恢复。

## 2. 运行模式与配置

默认配置位于 `config/pipeline.yaml`：

- `mode.source: online`
- `mode.llm: llm`
- `report_timezone: Asia/Shanghai`
- `report_language: zh-CN`
- `pipeline.llm_max_concurrency: 10`
- `pipeline.llm_business_retry_attempts: 1`

`mode.llm: llm` 是严格真实 LLM 模式，必须配置 `LLM_API_KEY` 并成功调用 OpenAI-compatible HTTP 接口。确定性规则 fallback 保留给显式 `auto`、离线、mock 或测试路径，不在默认真实模式中静默兜底。

`run_date` 可以写入配置，也可以通过 CLI 指定：

```bash
python -m src.main --run-date 2026-05-30
```

如果未配置 `run_date`，主入口会按 `report_timezone` 从当前 UTC 时间换算出报告日。

## 3. 文件流转

`config/pipeline.yaml` 中的 `paths` 是集成面，默认包括：

| 阶段 | latest 路径 |
| --- | --- |
| raw | `data/raw/ai_news_raw.json` |
| cleaned | `data/processed/ai_news_cleaned.json` |
| relevant | `data/processed/ai_news_relevant.json` |
| structured | `data/processed/ai_news_structured.json` |
| validated | `data/processed/ai_news_validated.json` |
| relevance report | `logs/relevance_report.json` |
| memory report | `logs/memory_report.json` |
| validation report | `logs/validation_report.json` |
| LLM audit | `logs/llm_audit_report.json` |
| report sections | `outputs/report_sections.json` |
| charts | `outputs/charts/` |
| report | `outputs/daily_report.md` |
| trace | `logs/run_trace.jsonl` |
| memory | `memory/topic_index.json`、`memory/items/*.json` |

每次运行还会创建 `state/runs/<run_id>/`，保存：

- `manifest.json`
- `config_snapshot.yaml`，其中 key、token、secret 等敏感字段会脱敏
- `metrics.json`
- `checkpoints/`
- `artifacts/` 下的 raw、cleaned、relevant、structured、validated、report、trace、charts、memory、memory items 等快照

## 4. 核心模块

### 4.1 Collector

实现位置：`src/pipeline/collect.py`、`src/adapters/sources.py`

Collector 通过 `create_source_adapter(config)` 创建数据源适配器，输出 `RawNewsItem`。它只负责采集和统一字段，不做 LLM 抽取或分析。

当前 online 模式启用的来源：

- TechCrunch RSS
- The Verge RSS
- arXiv API
- GitHub Releases
- Hacker News API
- Google News RSS
- 量子位 RSS
- IT之家 RSS

本地 fixture 源 `local_static_ai_news` 默认 disabled，只在 `mode.source: local_fixture` 中用于稳定测试。X、Reddit、Product Hunt 等需要凭证或策略确认的来源继续保持 disabled。

采集阶段应尽量保留可获得的完整正文或发布内容，LLM token 控制不放在 collector 中做。

### 4.2 Cleaner

实现位置：`src/pipeline/clean.py`

Cleaner 是确定性、非 LLM 步骤，职责包括：

- 文本空白规范化
- 必填字段过滤
- URL 规范化与 tracking query 清理
- 标题指纹与 `content_hash` 生成
- canonical URL、标题指纹、content hash 去重
- 按 `run_date + report_timezone` 做报告日硬过滤

如果 `published_at` 是 naive datetime，统一按 UTC 理解，再转换到报告时区判断是否属于 `run_date`。过滤后为空时直接失败。

### 4.3 Relevance Gate

实现位置：`src/pipeline/relevance.py`、`skills/ai_news_relevance/`

`relevance` 是 `clean` 和 `extract` 之间的硬准入门槛。它读取同日报 cleaned items，调用真实 LLM 或规则路径生成 `RelevanceAssessment`，并输出：

- `data/processed/ai_news_relevant.json`
- `logs/relevance_report.json`

`ai_news_relevance` Skill 提供规则、示例、输出 schema 和 validator。Pipeline 代码负责真正执行阈值和阻断，不把决策只交给文档或 Prompt。

### 4.4 Memory Strong Dedupe

实现位置：`hooks/post_relevance.py`、`src/harness/memory_manager.py`

该 Hook 在 `relevance` 后、`extract` 前执行。它从 Memory 中读取历史索引，用 `id`、`url`、`content_hash` 做强重复过滤。命中强重复的条目不允许进入抽取、分析或报告。

结果写入 `logs/memory_report.json` 的 `strong_dedupe` 段。如果所有 relevant items 都被过滤，流程失败并保留可诊断错误。

### 4.5 Extractor

实现位置：`src/pipeline/extract.py`、`prompts/extract_schema.md`、`skills/news_extraction/`

Extractor 只接收 `relevant` 输入，不再从 cleaned items 旁路进入。真实 LLM 模式会按单条 item 并行调用 Adapter，并对输出做 `StructuredNewsItem` 校验。离线或允许 fallback 的路径使用确定性规则抽取。

`StructuredNewsItem` 包含：

- canonical topic
- entities、event_type、summary、key_points
- sentiment、impact_scope
- `importance_score`
- `importance_rationale`
- `risk_level`、`risk_rationale`
- `opportunity_level`、`opportunity_rationale`
- evidence 与 `evidence_sources`
- `content_hash`

当前 topic taxonomy 固定为 8 类：

- AI Agents
- Foundation Models
- AI Infrastructure
- AI Applications
- Developer Tools and Open Source
- AI Safety and Governance
- AI Research
- AI Business and Market

旧别名会规范化到 canonical topic，避免 Memory topic 发散。

### 4.6 Validator

实现位置：`src/pipeline/validate.py`、`src/harness/validation.py`

Validator 是结构化结果进入分析、报告和 Memory 前的硬门槛。它执行：

- Pydantic schema 校验
- report-date 二次硬校验
- rationale 非空且非泛化校验
- `evidence_sources` 反查相关输入内容，要求至少有 supported evidence
- LLM audit 报告写入 `logs/llm_audit_report.json`

校验通过后写：

- `data/processed/ai_news_validated.json`
- `logs/validation_report.json`

### 4.7 Memory Context Retrieval

实现位置：`hooks/pre_process.py`、`src/harness/memory_similarity.py`、`src/harness/memory_fulltext.py`

`pre_analyze` 在 validate 后运行，负责给 Analyzer 提供历史上下文。它生成两个输出：

- `PipelineContext.historical_context`：受预算限制的文本上下文，用于 LLM Prompt
- `PipelineContext.state["memory_context"]`：结构化历史信号，用于规则分析、趋势状态和审计

Memory v2 当前已落地：

- 按 topic 和时间窗口检索历史 metadata
- 软重复 / 延续关系判断
- 五态趋势信号：`new`、`continuing`、`heating_up`、`cooling_down`、`reversing`
- 单条全文文件 `memory/items/<memory_item_id>.json`
- 两段式全文读取，LLM 或启发式选择最多 5 条全文
- 上下文预算控制
- `logs/memory_report.json` 审计 strong dedupe、context retrieval、soft similarity、fulltext selection、memory write

默认预算：

- 每 topic 最多 10 条 metadata
- metadata 总上下文约 16000 字符
- 全文最多 5 条
- 每条全文最多约 2000 字符
- selector catalog 最多约 16000 字符

### 4.8 Visualizer

实现位置：`src/pipeline/visualize.py`

Visualizer 不调用 LLM，只基于 validated items 生成 PNG 图表：

- `outputs/charts/topic_distribution.png`
- `outputs/charts/importance_ranking.png`

当前使用 Matplotlib，并配置中文字体 fallback 和短标签。

### 4.9 Analyzer

实现位置：`src/pipeline/analyze.py`、`prompts/analyze_daily_report.md`、`skills/trend_analysis/`、`skills/risk_detection/`

Analyzer 读取 validated items、chart refs、`historical_context`、结构化 `memory_context`、trend signals 和本轮 clean/relevant 原文内容，生成 `DailyInsightReport`。

真实 LLM 输出必须通过 schema 校验，并且报告级内容会再经过 evidence audit：

- unsupported report items 会被移除
- 如果 top events 全部被移除，流程失败
- risk/opportunity insight 若和 evidence item 的等级不一致，会先局部修复或移除
- trend/risk Skill validator 继续做 fail-closed 校验

规则路径只作为显式离线、auto fallback 或测试路径。

### 4.10 Report Generator

实现位置：`src/pipeline/generate_report.py`

Report Generator 只组装 validated data、analysis sections 和 charts，不重新做评分、趋势、准入或 Memory 写入。当前输出为中文 Markdown，包含：

- 执行摘要
- 重点事件
- 深度分析正文
- 趋势洞察
- 风险与机会
- 图表
- 结构化新闻判断说明表
- 编号参考依据
- 已使用历史上下文提示

### 4.11 Memory Write

实现位置：`hooks/post_validate.py`

`post_validate` Hook 在 `generate_report` 成功后触发。它只在 fresh run 且日报文件存在时写 latest Memory。Replay / resume 默认不写 latest Memory，避免历史回放污染当前记忆。

Memory 写入包括：

- `memory/topic_index.json` 中的 topic 索引与轻元数据
- `memory/items/<memory_item_id>.json` 中的 validated、clean、relevant、metadata、run_id、run_date、source artifact paths

写入前会按 `id`、`url`、`content_hash` 去重。

## 5. Harness

Harness 是项目的工程治理层，主要组件包括：

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| PipelineContext | `src/harness/context.py` | 传递 run_id、run_date、config、paths、state、artifacts、historical_context |
| PipelineRunner | `src/harness/runner.py` | 执行步骤、触发 Hook、记录 trace、接入 checkpoint 和 run store |
| HookRegistry | `src/harness/hooks.py` | 注册并运行 pre/post/error hooks |
| JsonlTracer | `src/harness/tracer.py` | 写入步骤级 JSONL trace 和安全 metadata |
| Checkpointer | `src/harness/checkpointer.py` | 步骤开始前保护 latest 文件，失败时 rollback |
| RunStore | `src/harness/run_store.py` | 维护全局 manifest、per-run manifest、artifact snapshot、metrics |
| Metrics | `src/harness/metrics.py` | 聚合 counts、step duration、LLM tokens、cost、errors、warnings |
| MemoryManager | `src/harness/memory_manager.py` | Topic Memory 读写、强重复索引、单条 memory item 文件 |
| SkillRunner | `src/harness/skill_runner.py` | 加载 Skill context、注入 Prompt、执行 validator |
| LLM audit | `src/harness/llm_audit.py` | 结构化抽取和报告证据审计 |

Trace 和 metrics 不写完整 Prompt、完整 Response 或 API key，只记录模型、token、耗时、成功状态、成本估算和短错误摘要。

## 6. 本地 Web 运行控制台

项目已增加一个本地 Web 运行控制台，作为 CLI 之外的人工操作入口。该前端只做启动、状态展示、产物导航和报告阅读，不改变 pipeline 的业务边界。

当前 V2 能力：

- 技术形态：FastAPI + Jinja，本地运行，入口为 `python -m src.web.main`。
- 首页提供“开始今日运行”按钮，只启动 fresh run。
- 后端启动现有 `src.main` 主流程，同一时间只允许一个前端启动的运行。
- 页面通过轮询读取 `state/runs/<run_id>/manifest.json`、trace 和 metrics，展示步骤状态、关键计数、活动流和健康状态。
- 固定展示步骤：`collect -> clean -> relevance -> memory_dedupe -> extract -> validate -> visualize -> analyze -> generate_report`。
- 每个步骤展示 pending / running / succeeded / failed，并配中文说明。
- 监控台展示当前步骤聚焦信息、产物卡片、底部 stepper 和主要 artifact 入口。
- 报告生成后可切换到沉浸阅读器，渲染 Markdown 日报、内嵌图表、章节目录，并提供复制全文和下载 Markdown。

当前 Web 控制台暂不包含 replay / resume 按钮、配置编辑、多任务并发、取消运行或 WebSocket 实时推送。Replay / resume 仍先通过 CLI 使用。

## 7. Snapshot、Replay 与 Resume

默认运行命令：

```bash
python -m src.main
```

Replay / resume 命令：

```bash
python -m src.main --replay-run-id <run_id> --from raw
python -m src.main --replay-run-id <run_id> --from relevant
python -m src.main --replay-run-id <run_id> --from validated
```

语义：

- `--from raw`：恢复父运行 raw snapshot，跳过在线 collect，从 clean 继续，运行模式记为 `replay`。
- `--from relevant`：恢复父运行 relevant snapshot，从 extract 继续，运行模式记为 `resume`。
- `--from validated`：恢复父运行 validated snapshot，从 visualize、analyze、report 继续，运行模式记为 `resume`。
- replay / resume 会创建新的 run_id，不改写父运行。
- replay / resume 优先读取父运行归档的 Memory snapshot；如果缺失，会记录降级并读取 latest Memory。
- replay / resume 默认不写 latest Memory。

当前第一阶段只支持 `raw`、`relevant`、`validated` 三个边界，不支持任意步骤边界或 `cleaned` 边界。

## 8. 测试与验收

默认测试命令：

```bash
python -m unittest discover -q
```

当前本地验证结果：`214` 个测试通过。

真实 LLM smoke test：

```bash
python scripts/smoke_llm.py
```

该脚本需要真实 key，可能产生成本，只用于人工联调。

## 9. 仍保留的边界

- 默认模式不是纯离线 MVP，`mode.llm: llm` 需要真实 LLM key 和可用服务。
- 真实 LLM Provider 当前仅支持 OpenAI-compatible HTTP。
- 输出以 Markdown 和 PNG 为主，尚未支持 HTML、PDF 或 PPT。
- Google News 仍主要作为发现入口，publisher URL 解析和原站正文抓取仍需增强。
- Extractor / Analyzer 的长输入策略仍需继续增强，例如 token 分块、正文压缩、片段选择或 Map-Reduce。
- Memory 使用本地 JSON 文件，不使用数据库、向量库或图数据库。
- 当前没有定时调度、自动发布或长期外部存储。
- 当前本地 Web 前端 V2 已实现；默认只监听 `127.0.0.1:8000`。
