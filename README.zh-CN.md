# Frontier Signal

**每日筛选并排序前沿 AI 工程论文、技术思路与系统。**

每天扫描前沿实验室、工程博客、arXiv 和 Hacker News，经筛选、去重和总结后推送到
飞书、Slack、Discord、Telegram 或终端。

[English](./README.md) · [系统设计](./docs/design.md) ·
[参与贡献](./CONTRIBUTING.md)

---

## 关注什么

Frontier Signal 只关注 **AI 工程**，不收录融资消息和泛科技新闻。

| 类型 | 默认信息源 |
|---|---|
| 前沿实验室与平台 | Anthropic、OpenAI、Google DeepMind、Google AI、Google Research、Meta AI、Microsoft Research、Hugging Face、BAIR、PyTorch、Answer.AI |
| 研究者与工程博客 | Lilian Weng、Sebastian Raschka、Chip Huyen、Jay Alammar、Eugene Yan、Simon Willison、Phil Schmid、Interconnects、Latent Space、The Gradient |
| 论文与技术社区 | arXiv、Hacker News |

它不会按发布时间简单罗列内容，而是完成筛选、去重和总结，再组织成可以直接阅读的日报：

| 输入 | 输出 |
|---|---|
| 所有新文章 | 只保留与关注方向相关的内容 |
| 同一事件的重复报道 | URL 与语义双重去重 |
| 标题和原文摘要 | 精炼总结，以及「为什么重要」 |
| 按发布时间排列的信息流 | P0 必读、P1 延伸阅读和每周趋势总结 |

---

## 你会收到什么

每天从约 130 条候选内容中筛出 5–8 条。每条包含核心结论和「为什么重要」，并按
P0 必读、P1 延伸阅读组织，最后给出今日趋势和 30 分钟阅读建议：

![中文 AI Engineering Daily 预览](./docs/images/daily-preview-zh.png)

---

## 快速开始

环境要求：[uv](https://docs.astral.sh/uv/) 和 Python 3.11+。

### 1. 安装与预览

```bash
git clone https://github.com/ll-leung/ai-research-radar.git frontier-signal
cd frontier-signal
uv sync
uv run radar preview
```

`preview` 抓取最新内容并将结果打印到终端，不会发送消息。首次运行无需 API key 或
webhook；未配置模型时，项目会使用关键词完成筛选。每次预览都从全新状态开始，不会
影响之后的日报。

### 2. 配置模型与推送

```bash
uv run radar init
```

初始化向导分为四步：

1. 选择模型服务，并设置初筛模型和总结模型
2. 选择推送渠道，并填写对应的连接信息
3. 选择中文或英文报告
4. 设置每天的发送时间

完成后，配置会保存到 `.env`。向导会自动读取本机时区并更新 GitHub Actions 的运行
时间。输入 API key 或 webhook 时，终端不会显示内容；保存成功后会给出明确提示。

### 3. 测试配置

```bash
uv run radar test-notify  # 向当前渠道发送连接测试
uv run radar test-layout  # 向当前渠道发送排版示例
```

`test-layout` 使用内置示例内容，不抓取信息源，也不调用模型。还可以指定渠道或语言：

```bash
uv run radar test-layout --channel lark
uv run radar test-layout --channel all
uv run radar test-layout --language en
```

`all` 会跳过尚未完成配置的渠道，并在终端列出原因。

### 4. 运行日报

```bash
uv run radar preview  # 检查筛选与总结结果，不发送消息
uv run radar run      # 生成并发送日报
```

`run` 会记住已经处理过的内容，避免重复推送。如果当天没有新的入选内容，项目不会发送
空日报，并会在终端说明原因。

---

## 工作原理

```mermaid
flowchart TB
    subgraph L1[信息源层]
        direction LR
        LABS[前沿实验室与平台]
        BLOGS[研究者与工程博客]
        ARXIV[arXiv]
        HN[Hacker News]
    end

    subgraph L2[采集与数据层]
        direction LR
        ADAPTERS[Source Adapters] --> FETCH[并发抓取]
        FETCH --> NORMALIZE[规范化与 URL 去重]
    end

    subgraph L3[智能处理层]
        direction LR
        RULES[规则初筛] --> RELEVANCE[相关性判断]
        RELEVANCE --> DEDUPE[语义去重与主题聚类]
        DEDUPE --> RANK[综合排序 · P0 / P1]
        RANK --> SYNTHESIS[总结 · 趋势 · 阅读建议]
    end

    subgraph L4[报告与渠道层]
        direction LR
        REPORT[统一 Report 模型] --> NOTIFIER[Notifier Interface]
        NOTIFIER --> CARD[飞书卡片]
        NOTIFIER --> WEBHOOK[Slack · Discord · Telegram]
        NOTIFIER --> CONSOLE[Console]
    end

    subgraph MODEL_LAYER[模型层]
        direction LR
        MODEL[Model Interface] --> FILTER_MODEL[初筛模型]
        MODEL --> SUMMARY_MODEL[总结模型]
        MODEL --> EMBEDDING[Local Embedding]
        FILTER_MODEL --> PROVIDERS[Anthropic · OpenAI · Ollama]
        SUMMARY_MODEL --> PROVIDERS
        EMBEDDING --> BGE[bge-small]
    end

    STORE[(SQLite<br/>抓取进度 · 历史记录 · 向量缓存)]

    L1 --> L2 --> L3 --> L4
    L3 -. 使用 .-> MODEL_LAYER
    L2 -. 读写 .-> STORE
    L3 -. 读写 .-> STORE

    classDef source fill:#1d4ed8,stroke:#93c5fd,color:#ffffff
    classDef ingest fill:#0f766e,stroke:#5eead4,color:#ffffff
    classDef process fill:#6d28d9,stroke:#c4b5fd,color:#ffffff
    classDef model fill:#0369a1,stroke:#7dd3fc,color:#ffffff
    classDef delivery fill:#b45309,stroke:#fcd34d,color:#ffffff
    classDef storage fill:#334155,stroke:#94a3b8,color:#ffffff

    class LABS,BLOGS,ARXIV,HN source
    class ADAPTERS,FETCH,NORMALIZE ingest
    class RULES,RELEVANCE,DEDUPE,RANK,SYNTHESIS process
    class MODEL,FILTER_MODEL,SUMMARY_MODEL,EMBEDDING,PROVIDERS,BGE model
    class REPORT,NOTIFIER,CARD,WEBHOOK,CONSOLE delivery
    class STORE storage

    style L1 fill:#eff6ff,stroke:#2563eb,stroke-width:2px
    style L2 fill:#f0fdfa,stroke:#0d9488,stroke-width:2px
    style L3 fill:#faf5ff,stroke:#7e22ce,stroke-width:2px
    style MODEL_LAYER fill:#f0f9ff,stroke:#0284c7,stroke-width:2px
    style L4 fill:#fffbeb,stroke:#d97706,stroke-width:2px
```

主流程按内容流向展开：从前沿实验室、研究者博客、arXiv 和 Hacker News 获取内容，
通过统一的 Source Adapter 并发抓取、规范化并完成 URL 去重，再进入智能处理层。

智能处理层依次完成规则初筛、LLM 相关性判断、语义去重、主题聚类和综合排序，最终生成
P0 / P1、逐条总结、今日趋势和阅读建议。

模型层通过统一接口提供初筛、总结和 embedding。处理流程不依赖具体厂商，因此初始化时
选择 Anthropic、OpenAI、Ollama 或其他兼容服务后，后续流程无需改变。

报告与渠道层先生成统一的 Report，再由 Notifier 分别渲染飞书卡片、Webhook 消息或
终端输出。SQLite 独立保存抓取进度、历史文章和向量缓存。CLI 与 GitHub Actions 只是
两种启动方式，共用图中的完整处理流程。

---

## 模型与推送渠道

运行 `radar init` 时，可以直接选择模型服务、初筛模型和总结模型。之后切换模型只需
修改 `.env`。

| 层级 | 支持选项 |
|---|---|
| 模型服务 | Anthropic、OpenAI、Ollama、其他 OpenAI 兼容服务，或仅使用关键词筛选 |
| 推送 | 飞书 / Lark、Slack、Discord、Telegram、console |
| 语言 | 中文（`zh`）或英文（`en`） |

所有设置见 [`.env.example`](./.env.example)。飞书会渲染可折叠卡片，其他推送渠道
使用 Markdown。

---

## 每日自动运行

项目内置日报和周报的 GitHub Actions 工作流。配置完成后，无需保持本机或服务器运行；
GitHub 会按设定时间抓取内容、生成报告并发送到目标渠道。

### 部署到 GitHub Actions

1. Fork 本仓库，并将 Fork 后的仓库克隆到本地。
2. 运行 `uv run radar init`，完成模型、推送渠道、语言和发送时间设置。
3. 向导结束时会列出 GitHub Actions 需要的 Variables 和 Secrets。打开仓库的
   **Settings → Secrets and variables → Actions**，按提示逐项添加。
4. `radar init` 会根据本机时区更新日报时间。提交并推送
   [`.github/workflows/daily.yml`](./.github/workflows/daily.yml) 的修改。
5. 打开仓库的 **Actions → Frontier Signal Daily → Run workflow**，手动运行一次，确认目标渠道
   能收到日报。验证通过后，工作流会按计划自动运行。

普通配置放在 Variables，敏感信息放在 Secrets：

| 类型 | 内容 |
|---|---|
| Variables | 模型服务、初筛模型、总结模型、推送渠道、报告语言，以及可选的 OpenAI-compatible 地址 |
| Secrets | 模型 API key，以及飞书 webhook、Slack webhook、Telegram token 等渠道凭据 |

配置完成后，`Frontier Signal Daily` 会每天自动发送日报，也可以随时在 Actions 页面手动运行。
`Frontier Signal Weekly` 汇总最近七天的入选内容并生成周报；需要修改周报时间时，编辑
[`.github/workflows/weekly.yml`](./.github/workflows/weekly.yml) 中的 cron 设置。

工作流会保留已经处理过的文章，避免后续日报重复推送；这些历史记录只保存在 GitHub
Actions 缓存中，不会写入仓库。本地 embedding 模型也会被缓存，以减少后续运行的启动
时间。

使用 Anthropic、OpenAI 等在线模型服务时无需额外处理。如果选择 Ollama，GitHub Actions
不能直接连接你电脑上的 `localhost`；需要使用 GitHub runner 可以访问的 Ollama 地址，
或者在自己的机器上运行 self-hosted runner。

---

## 开发与扩展

安装开发依赖并运行质量检查：

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

测试使用本地 fixture 和 HTTP mock，不需要网络、模型 API key 或 webhook。修改筛选、
去重、排序和排版后，应先运行完整测试，再使用以下命令检查真实输出：

```bash
uv run radar preview                         # 运行一次独立预览
uv run radar test-layout --channel console  # 检查 Markdown 输出
uv run radar test-layout --channel lark     # 检查飞书卡片
uv run radar sources                         # 查看各信息源的抓取状态
```

项目通过小型接口隔离信息源、模型和推送渠道，新增能力不需要修改整条处理流程：

| 扩展内容 | 修改位置 |
|---|---|
| 新增 RSS / Atom 信息源 | 在 [`config/sources.yaml`](./config/sources.yaml) 添加配置 |
| 新增 API 信息源 | 在 [`radar/sources/`](./radar/sources/) 实现抓取逻辑 |
| 新增模型服务 | 实现 [`LLMClient`](./radar/llm/base.py)，并接入 [`factory.py`](./radar/llm/factory.py) |
| 新增推送渠道 | 实现 [`Notifier`](./radar/notify/base.py)，并接入 [`factory.py`](./radar/notify/factory.py) |
| 新增报告语言 | 在 [`radar/i18n.py`](./radar/i18n.py) 添加标签和模型写作要求 |
| 修改筛选与排序 | 调整 [`config/interests.yaml`](./config/interests.yaml) 或 [`radar/pipeline/`](./radar/pipeline/) |

提交代码前请确保测试与 Ruff 检查通过。贡献规范见
[CONTRIBUTING.md](./CONTRIBUTING.md)。

## 配置参考

运行 `radar init` 可以完成常规设置；需要精细调整时，再直接修改以下文件或环境变量：

| 修改内容 | 文件 |
|---|---|
| 信息源、名称和来源权重 | [`config/sources.yaml`](./config/sources.yaml) |
| 关注主题、关键词权重、排除规则、日报条数和单一来源配额 | [`config/interests.yaml`](./config/interests.yaml) |
| 模型服务、模型名称、推送渠道、报告语言和连接信息 | [`.env.example`](./.env.example) |
| 相关性判断标准 | [`radar/pipeline/llm_filter.py`](./radar/pipeline/llm_filter.py) |
| 中英文固定文案与写作要求 | [`radar/i18n.py`](./radar/i18n.py) |

`.env` 保存当前机器的运行配置和敏感信息，不应提交到 Git。YAML 文件适合保存可以进入
版本控制的内容策略，例如信息源、主题权重和来源配额。环境变量会覆盖 `.env` 中的同名
设置，GitHub Actions 正是通过这种方式加载 Variables 和 Secrets。

常用的精细参数包括 `LLM_MIN_RELEVANCE`、`LLM_MAX_CANDIDATES`、
`TRUSTED_AUTHORITY`、`COLD_START_DAYS`、`MAX_CONCURRENCY` 和
`PER_SOURCE_CAP`；默认值见 [`radar/config.py`](./radar/config.py)。

## 项目边界

Frontier Signal 聚焦 AI 工程研究与生产实践。它的核心任务是从公开信息源中筛选、
去重、排序和总结值得工程师投入时间的内容。

项目当前不提供全文收藏、稍后阅读、团队知识库、用户反馈训练或托管 SaaS。它也不追求
覆盖所有 AI 新闻；融资、政策、泛科技新闻和营销内容默认排除。新增来源和功能应提高
信号质量，或让模型与渠道更容易替换，而不是单纯扩大收录数量。

GitHub release 与主流 AI framework release 的结构化跟踪仍在计划中。

## 许可证

[MIT](./LICENSE) © Lili Liang
