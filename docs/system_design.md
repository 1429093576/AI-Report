# Daily AI Insight Engine 系统设计说明

## 1. 项目目标与当前边界

Daily AI Insight Engine 是一个面向 AI 科技新闻的日报生成系统。它从公开在线来源采集候选新闻，经过清洗、同日报过滤、AI 科技相关性准入、历史重复过滤、结构化抽取、校验、历史上下文检索、可视化和分析，最终生成中文 Markdown 日报、PNG 图表、运行指标、审计报告和 Topic Memory。

当前端到端流程：

```text
collect -> clean -> relevance -> memory_dedupe -> extract -> validate
  -> pre_analyze memory context -> visualize -> analyze -> generate_report
  -> post_validate memory write
```

当前 MVP 已覆盖：

- 在线多源采集
- 报告日硬过滤
- AI 科技相关新闻准入 Gate
- Memory 强重复过滤
- 真实 LLM 抽取、分析和 Memory 全文选择
- 离线 / 测试路径的确定性 fallback
- Pydantic Schema 校验
- Evidence audit 与 LLM audit
- Markdown 报告和 PNG 图表
- Run manifest、per-run snapshot、checkpoint / rollback、metrics
- 从 `raw`、`relevant`、`validated` 快照 replay / resume

当前边界：

- 默认不是纯离线模式。`config/pipeline.yaml` 使用 `mode.llm: llm`，必须配置 `LLM_API_KEY` 并成功调用真实 LLM。
- 真实 Provider 当前只支持 OpenAI-compatible Chat Completions HTTP。
- 输出以 Markdown 和 PNG 为主，尚未实现 HTML、PDF、PPT。
- Memory 使用本地 JSON，不引入数据库、向量库或图数据库。
- replay / resume 第一阶段只支持 `raw`、`relevant`、`validated` 三个边界。

## 2. 默认运行配置

关键配置位于 `config/pipeline.yaml`：

```yaml
report_timezone: Asia/Shanghai
report_language: zh-CN

mode:
  source: online
  llm: llm

pipeline:
  llm_max_concurrency: 10
  llm_business_retry_attempts: 1
  fetch_full_content: true
  retry_attempts: 3
```

运行命令：

```bash
python -m src.main
```

指定报告日：

```bash
python -m src.main --run-date 2026-05-30
```

Replay / resume：

```bash
python -m src.main --replay-run-id <run_id> --from raw
python -m src.main --replay-run-id <run_id> --from relevant
python -m src.main --replay-run-id <run_id> --from validated
```

`src.main` 启动时会加载 `.env`，解析配置、路径、`run_date`，注册 Hook，并由 `PipelineRunner` 执行步骤。未显式配置 `run_date` 时，系统按 `report_timezone` 从当前时间计算报告日。

## 3. 数据源设计

采集结果统一写入 `data/raw/ai_news_raw.json`。默认 `mode.source: online` 只读取 enabled 的在线来源；本地 fixture 源保留但默认 disabled。

当前 enabled 来源：

| 来源 | 类型 | 处理方式 | 状态 |
| --- | --- | --- | --- |
| TechCrunch | RSS | RSS 发现，尝试抓取原文 HTML 正文，失败回退摘要 | enabled |
| The Verge | RSS | RSS 发现，尝试抓取原文 HTML 正文，失败回退摘要 | enabled |
| arXiv | Atom API | 保存论文标题、摘要、作者、分类和链接 | enabled |
| GitHub Releases | API | release `body` 作为发布正文，记录 repo/tag/prerelease | enabled |
| Hacker News | Firebase API | 获取 story 元数据，有外链时尝试抓取外链正文 | enabled |
| Google News | RSS | 作为发现入口，保留聚合元数据，publisher URL 解析仍需增强 | enabled |
| 量子位 | RSS | 中文 RSS 发现，尝试抓取详情页正文 | enabled |
| IT之家 | RSS | 中文 RSS 发现，使用 AI 关键词预筛候选，再由 relevance Gate 决定准入 | enabled |

当前 disabled 来源：

- X：需要凭证
- Reddit：API/RSS 策略待定
- Product Hunt：需要凭证
- local static fixture：只用于 `mode.source: local_fixture`

统一约束：

- 采集阶段不做 LLM 抽取或分析。
- `RawNewsItem.content` 尽量保留完整可获得内容。
- 所有 Adapter 应写入 `metadata.content_source`，例如 `article_html`、`rss_feed`、`github_release_body`、`arxiv_summary`、`social_post`、`aggregator_snippet`。
- 单源失败写入 source metrics / trace，除非所有 enabled 来源都失败，否则不阻断整个 collect。
- source-level `lookback_days` 只是候选窗口，不能替代 `clean` 的报告日硬过滤。

## 4. 数据合同

Schema 位于 `src/schemas/`，核心模型包括：

- `RawNewsItem`
- `CleanNewsItem`
- `RelevanceAssessment`
- `StructuredNewsItem`
- `DailyInsightReport`
- `ChartSpec`
- `ValidationResult`

`StructuredNewsItem` 当前包含读者可解释的判断依据字段：

- `importance_score`
- `importance_rationale`
- `risk_level`
- `risk_rationale`
- `opportunity_level`
- `opportunity_rationale`
- `evidence`
- `evidence_sources`

Topic taxonomy 固定为 8 类：

- `AI Agents`
- `Foundation Models`
- `AI Infrastructure`
- `AI Applications`
- `Developer Tools and Open Source`
- `AI Safety and Governance`
- `AI Research`
- `AI Business and Market`

旧 topic 别名会在 schema 层规范化，未知 topic 会失败，避免下游报告和 Memory 索引发散。

## 5. Pipeline 模块设计

### Collector

实现位置：`src/pipeline/collect.py`

Collector 调用 `create_source_adapter(config)`，采集后用 `RawNewsItem` 校验并写入 raw latest 文件。它会把 `source_errors` 和 `source_metrics` 放入 `PipelineContext.state`，供 trace 和 metrics 聚合。

### Cleaner

实现位置：`src/pipeline/clean.py`

Cleaner 是确定性非 LLM 模块，负责：

- 文本规范化
- 必填字段过滤
- URL 规范化和 tracking 参数移除
- canonical URL、标题指纹、content hash 去重
- 按 `run_date + report_timezone` 过滤非同日报新闻
- 输出 clean quality metadata

naive `published_at` 按 UTC 解释后再转换到报告时区。过滤后为空时直接失败。

### Relevance Gate

实现位置：`src/pipeline/relevance.py`、`skills/ai_news_relevance/`

Relevance Gate 读取 cleaned items，使用真实 LLM 或规则路径生成 `RelevanceAssessment`，通过条件固定为：

```text
is_ai_related = true
relevance_score >= 70
```

输出：

- `data/processed/ai_news_relevant.json`
- `logs/relevance_report.json`

`SkillRunner` 会加载 `ai_news_relevance` Skill 的规则、示例和输出契约，并执行 validator。没有通过准入的条目不会进入 extract。

### Memory Strong Dedupe

实现位置：`hooks/post_relevance.py`

该 Hook 在 `relevance` 之后运行，用历史 Memory 的 `id`、`url`、`content_hash` 强过滤重复事件。结果写入 `logs/memory_report.json` 的 `strong_dedupe` 段。

如果所有 relevant items 都命中历史强重复，流程失败，避免重复旧事件进入今日抽取和报告。

### Extractor

实现位置：`src/pipeline/extract.py`

Extractor 只接受 relevant items。真实 LLM 路径按单条 item 并发调用，Prompt 来自 `prompts/extract_schema.md`，Skill context 来自 `skills/news_extraction/`。输出必须能校验为 `StructuredNewsItem`。

显式 `mode.llm: llm` 下，LLM transport error、非法 JSON、schema error 或 item count mismatch 会在业务层重试后 fail-fast。`auto` / 离线 / 测试路径才允许回退到规则抽取。

### Validator

实现位置：`src/pipeline/validate.py`、`src/harness/validation.py`

Validator 做四类硬校验：

- `StructuredNewsItem` schema 校验
- `published_at` 仍属于 `run_date` 的二次校验
- 评分和等级 rationale 不能是泛化空话
- `evidence_sources` 至少有一条能反查到 source item 的 supported evidence

校验报告写入 `logs/validation_report.json`，证据审计写入 `logs/llm_audit_report.json`。

### Visualizer

实现位置：`src/pipeline/visualize.py`

Visualizer 只基于 validated items 绘图，不调用 LLM。当前生成：

- `outputs/charts/topic_distribution.png`
- `outputs/charts/importance_ranking.png`

### Analyzer

实现位置：`src/pipeline/analyze.py`

Analyzer 读取：

- validated items
- chart refs
- `PipelineContext.historical_context`
- `PipelineContext.state["memory_context"]`
- trend signals
- 当前 relevant / clean 对应的 source documents

真实 LLM 输出必须校验为 `DailyInsightReport`。随后系统会进行报告级证据审计，移除 unsupported report items；如果 `top_events` 被全部移除，则流程失败。风险和机会 insight 会额外检查 evidence item 的等级一致性，不一致时局部修复或删除。

Analyzer 同时使用：

- `skills/trend_analysis/`
- `skills/risk_detection/`

### Report Generator

实现位置：`src/pipeline/generate_report.py`

Report Generator 只做 Markdown 组装，不重新计算准入、评分、趋势或 Memory。当前中文报告包含：

- 执行摘要
- 重点事件
- 深度分析正文
- 趋势洞察
- 风险与机会
- 图表
- 结构化新闻判断说明表
- 编号参考依据
- 已使用历史上下文提示

## 6. LLM 使用方式

当前默认：

```yaml
mode:
  llm: llm
```

这意味着：

- 必须配置 `LLM_API_KEY`。
- `relevance`、`extract`、`analyze` 和 Memory fulltext selector 会使用统一 LLM Adapter。
- 业务层重试次数由 `pipeline.llm_business_retry_attempts` 控制，当前为 1 次重试。
- 显式真实模式中不静默 fallback 到规则结果。

允许 fallback 的模式包括 `auto`、`mock`、`offline`、`rule_based` 等，主要用于测试、离线演示或降级路径。

LLM 观测信息：

- `logs/run_trace.jsonl` 记录步骤生命周期和 LLM 安全摘要。
- `logs/metrics.json` 汇总 token、cost、duration、error、fallback 等信息。
- `logs/llm_audit_report.json` 汇总结构化证据和报告证据审计。
- 不落完整 Prompt、完整 Response 或 API key。

## 7. Memory v2 设计

Memory 的定位：

- 不是新闻源。
- 不是 AI 相关性准入裁判。
- 是历史上下文层和重复事件防线。
- 今日事实仍只能来自 `run_date` 当天且通过 `relevance -> extract -> validate` 的数据。

### 存储结构

`memory/topic_index.json` 保存 topic 索引、核心字段、轻元数据和单条全文文件路径。

`memory/items/<memory_item_id>.json` 保存：

- schema version
- memory item id
- run id
- run date
- created at
- topic
- validated structured item
- clean item
- relevant item
- merged metadata
- source metadata
- source artifact paths

### 运行阶段

Memory v2 当前覆盖五个阶段：

| 阶段 | 触发点 | 输出 |
| --- | --- | --- |
| strong dedupe | relevance 后 | `memory_report.strong_dedupe` |
| context retrieval | validate 后 / analyze 前 | `historical_context`、`memory_context`、`memory_report.context_retrieval` |
| soft similarity | context retrieval 中 | `memory_context.item_relationships`、`memory_report.soft_similarity` |
| fulltext selection | context retrieval 中 | 选中 memory item 全文片段、`memory_report.fulltext_selection` |
| memory write | generate_report 成功后 | `memory/topic_index.json`、`memory/items/*.json`、`memory_report.memory_write` |

### 趋势状态

趋势状态模型：

- `new`
- `continuing`
- `heating_up`
- `cooling_down`
- `reversing`

规则会生成 `rule_suggested_state` 和结构化 trend signals。真实 LLM 模式下，最终 `trend_state` 由 LLM 综合今日数据、历史上下文、结构化 Memory context 和 trend signals 判断。

### Replay / Resume Memory 语义

- fresh run 且 `generate_report` 成功后才写 latest Memory。
- replay / resume 默认不写 latest Memory。
- replay / resume 读取时优先使用父运行归档的 Memory snapshot，避免回放结果随 latest Memory 漂移。
- 如果父运行没有可用 Memory snapshot，会在 Memory report / trace 中记录降级。

## 8. Harness Engineering

项目使用轻量自建 Harness，而不是引入完整 Agent 框架。

| 能力 | 实现位置 | 说明 |
| --- | --- | --- |
| Runner | `src/harness/runner.py` | 执行步骤、Hook、trace、checkpoint、manifest |
| Context | `src/harness/context.py` | 传递 config、paths、state、artifacts、historical_context |
| Hook | `src/harness/hooks.py`、`hooks/` | pre_analyze、post_validate、on_error、post_relevance |
| Trace | `src/harness/tracer.py` | JSONL 步骤事件和安全 metadata |
| Checkpoint | `src/harness/checkpointer.py` | 步骤开始前保护 latest 文件，失败 rollback |
| RunStore | `src/harness/run_store.py` | per-run snapshot、manifest、config snapshot、metrics |
| Metrics | `src/harness/metrics.py` | 运行健康、耗时、计数、LLM token / cost、错误摘要 |
| SkillRunner | `src/harness/skill_runner.py` | 加载 Skill context、注入 Prompt、执行 validator |
| Memory | `src/harness/memory_manager.py` | 本地 Topic Memory、强重复、单条全文文件 |
| Audit | `src/harness/llm_audit.py` | 结构化与报告证据审计 |

Checkpointer 当前保护的 latest 产物包括 raw、cleaned、relevant、structured、validated、validation report、report sections、charts、daily report 和 memory 等。步骤失败时会恢复该步骤开始前的状态，并在 manifest 中记录 rollback 结果。

## 9. Run Snapshot、Metrics 与 Replay

每次运行生成一个 `run_id`，目录为：

```text
state/runs/<run_id>/
  config_snapshot.yaml
  manifest.json
  metrics.json
  checkpoints/
  artifacts/
```

全局索引：

- `state/run_manifest.json`

latest metrics：

- `logs/metrics.json`

最近一次记录的真实在线运行：

- run id：`run-web-90b94cbb984243048b234b72f2666671`
- run date：`2026-05-31`
- mode：`fresh`
- status：`succeeded`
- health：`healthy`
- counts：`raw 80 -> cleaned 32 -> relevant 18 -> structured 18 -> validated 18`
- artifacts：Markdown 日报、2 张图表、validation report、LLM audit、Memory report、Memory snapshot、trace snapshot、metrics 均已归档

## 10. 输出结果

主要 latest 输出：

| 路径 | 说明 |
| --- | --- |
| `outputs/daily_report.md` | 中文日报 |
| `outputs/report_sections.json` | Analyzer 输出的结构化日报段落 |
| `outputs/charts/topic_distribution.png` | 主题分布图 |
| `outputs/charts/importance_ranking.png` | 关注度排行图 |
| `data/processed/ai_news_validated.json` | 校验通过的结构化新闻 |
| `logs/relevance_report.json` | AI 科技相关性准入审计 |
| `logs/validation_report.json` | Schema 校验报告 |
| `logs/llm_audit_report.json` | 结构化证据与报告证据审计 |
| `logs/memory_report.json` | Memory 强重复、检索、软相似、全文选择、写入审计 |
| `logs/metrics.json` | 最新运行健康状态与指标摘要 |
| `logs/run_trace.jsonl` | JSONL trace |
| `memory/topic_index.json` | Topic Memory 索引 |
| `memory/items/*.json` | 单条全文 Memory 文件 |

## 11. 本地 Web 运行控制台

项目已增加本地 Web 运行控制台，服务于人工启动、观察 pipeline 运行过程和阅读最终日报。

当前 V2 设计口径：

- 技术形态：FastAPI + Jinja，本地运行。
- 运行入口为 `python -m src.web.main`。
- 首页提供“开始今日运行”按钮，只触发一次完整 fresh run。
- 前端状态通过轮询刷新，不使用 WebSocket / SSE。
- 状态来源复用现有 `RunStore` 与 `JsonlTracer` 产物：
  - `state/runs/<run_id>/manifest.json`
  - `logs/run_trace.jsonl`
  - `state/runs/<run_id>/metrics.json`
- 监控台步骤状态展示固定覆盖：
  - `collect`
  - `clean`
  - `relevance`
  - `memory_dedupe`
  - `extract`
  - `validate`
  - `visualize`
  - `analyze`
  - `generate_report`
- 每个步骤展示 pending、running、succeeded、failed 和中文说明，例如 collect 表示采集来源，clean 表示清洗去重和报告日过滤。
- 全景监控台展示当前步骤聚焦信息、活动流、关键计数、健康状态、产物卡片和底部 stepper。
- 沉浸阅读器在报告生成后渲染 Markdown 日报，展示章节目录、内嵌图表，并提供复制全文和下载 Markdown。
- 运行失败时展示失败步骤、错误类型和短错误信息。

当前 Web 控制台明确不做：

- replay / resume 按钮
- 配置编辑
- 多任务并发
- 取消运行
- 实时推送

这样先把“从页面开始运行、看见每一步做到哪里、并阅读最终日报”的闭环做稳，后续再把历史运行详情、replay / resume 和导出能力加进去。

## 12. 测试状态

默认测试命令：

```bash
python -m unittest discover -q
```

当前结果：`214` 个测试通过。

真实 LLM smoke test：

```bash
python scripts/smoke_llm.py
```

该脚本需要真实 key，会产生真实 API 调用和成本，只用于人工联调。

## 13. 已知限制与后续方向

当前限制：

- Google News publisher URL 解析和原站正文抓取仍需增强。
- 中文媒体 HTML 正文补全仍需继续打磨。
- Extractor / Analyzer 长输入策略还可以继续增强，例如 token 分块、正文压缩、片段选择、Map-Reduce 抽取。
- 当前图表只有主题分布和关注度排行。
- 还没有 HTML、PDF、PPT 导出。
- 本地 Web 运行控制台 V2 已实现，但还没有 replay / resume 按钮、配置编辑、多任务并发或取消运行。
- 还没有自动调度和发布。
- 还没有长期数据库、向量库或图数据库。

优先后续方向：

- 提升在线正文质量和 source error 诊断。
- 增强 Google News 到 publisher URL 的解析。
- 为长文本抽取和分析增加预算内选择策略。
- 增强本地 Web 运行控制台，补充历史运行详情、replay / resume 和更丰富的产物浏览。
- 增加 HTML / PDF / PPT 导出。
- 增加每日调度方案。
