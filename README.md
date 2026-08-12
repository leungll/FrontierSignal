# Frontier Signal

Frontier Signal is a frontier-tracking tool built for AI engineers. It
automatically follows the latest developments across leading research labs,
engineering blogs, arXiv, and Hacker News, helping you quickly find what is
actually worth your attention.

It uses AI models to assist with content filtering, automatically removes
duplicates, and generates concise summaries. The results are ranked by importance
into daily and weekly selections, then delivered to Lark, Slack, Discord,
Telegram, or your terminal.

It focuses on agent systems, model serving, evaluations, context engineering,
memory, MCP, tool use, AI coding, RAG, RSI (recursive self-improvement),
distributed training and inference, and production AI infrastructure—while
filtering out funding, policy, and generic tech news.

[简体中文](./README.zh-CN.md) · [System design](./docs/design.md) ·
[Contributing](./CONTRIBUTING.md)

![Author](https://img.shields.io/badge/Author-Lili_Liang-red)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-Anthropic_·_OpenAI_·_Ollama-6E56CF)
![Embeddings](https://img.shields.io/badge/Embeddings-bge--small-0EA5E9)
![uv](https://img.shields.io/badge/uv-managed-DE5FE9)
![Last commit](https://img.shields.io/github/last-commit/leungll/FrontierSignal?color=yellow)
![Repo size](https://img.shields.io/github/repo-size/leungll/FrontierSignal)
![Top language](https://img.shields.io/github/languages/top/leungll/FrontierSignal?color=orange)

---

## What it tracks

Frontier Signal covers **AI engineering**, not funding rounds or general
technology news.

| Group | Default sources |
|---|---|
| Frontier labs and platforms | Anthropic, OpenAI, Google DeepMind, Google AI, Google Research, Meta AI, Microsoft Research, Hugging Face, BAIR, PyTorch, Answer.AI |
| Researchers and engineering blogs | Lilian Weng, Sebastian Raschka, Chip Huyen, Jay Alammar, Eugene Yan, Simon Willison, Phil Schmid, Interconnects, Latent Space, The Gradient |
| Research and community | arXiv, Hacker News |

Instead of returning a chronological feed, it filters, deduplicates, summarizes,
and organizes the material into a report ready to read:

| Input | Output |
|---|---|
| Every new post | Only items that clear the relevance gate |
| Repeated coverage | URL and semantic deduplication |
| Headlines and excerpts | A concise summary and why it matters |
| A chronological stream | P0 must-reads, P1 follow-ups, and a weekly synthesis |

---

## What arrives

Each run reduces roughly 130 candidates to 5–8 items. Every item includes the
core finding and why it matters, organized into P0 must-reads and P1 follow-ups,
followed by the day's trend and a 30-minute reading plan:

![English AI Engineering Daily preview](./docs/images/daily-preview-en.png)

---

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.11+.

### 1. Install and preview

```bash
git clone https://github.com/leungll/FrontierSignal.git frontier-signal
cd frontier-signal
uv sync
uv run radar preview
```

`preview` fetches the latest content and prints the result to the terminal without
sending a message. The first run needs neither an API key nor a webhook; if no
model is configured, keyword scoring is used. Every preview starts fresh and does
not affect later reports.

### 2. Configure models and delivery

```bash
uv run radar init
```

The setup wizard has four steps:

- **Step 1:** Choose a provider and set the filtering and summary models
- **Step 2:** Choose a delivery channel and enter its connection details
- **Step 3:** Choose English or Chinese reports
- **Step 4:** Set the daily delivery time

The result is saved to `.env`. The wizard detects the local timezone and updates
the GitHub Actions schedule. API keys and webhooks are hidden while you enter
them, and the wizard confirms when each value has been saved.

### 3. Test the configuration

```bash
uv run radar test-notify  # send a connection test to the configured channel
uv run radar test-layout  # send a layout sample to the configured channel
```

`test-layout` uses built-in sample content, so it fetches no sources and calls no
model. You can also target a channel or language explicitly:

```bash
uv run radar test-layout --channel lark
uv run radar test-layout --channel all
uv run radar test-layout --language en
```

`all` skips channels without credentials and reports each skip in the terminal.

### 4. Run the daily report

```bash
uv run radar preview  # inspect filtering and summaries without delivery
uv run radar run      # generate and deliver the daily report
```

`run` remembers processed items to prevent duplicate delivery. If nothing new is
selected, it sends no empty report and explains why in the terminal.

---

## How it works

```mermaid
flowchart TB
    subgraph L1[Source layer]
        direction LR
        LABS[Frontier labs and platforms]
        BLOGS[Researchers and engineering blogs]
        ARXIV[arXiv]
        HN[Hacker News]
    end

    subgraph L2[Ingestion and data layer]
        direction LR
        ADAPTERS[Source Adapters] --> FETCH[Concurrent fetch]
        FETCH --> NORMALIZE[Normalization and URL dedupe]
    end

    subgraph L3[Intelligence layer]
        direction LR
        RULES[Rule prefilter] --> RELEVANCE[Relevance evaluation]
        RELEVANCE --> DEDUPE[Semantic dedupe and clustering]
        DEDUPE --> RANK[Combined ranking · P0 / P1]
        RANK --> SYNTHESIS[Summary · trend · reading plan]
    end

    subgraph L4[Report and channel layer]
        direction LR
        REPORT[Shared Report model] --> NOTIFIER[Notifier Interface]
        NOTIFIER --> CARD[Lark card]
        NOTIFIER --> WEBHOOK[Slack · Discord · Telegram]
        NOTIFIER --> CONSOLE[Console]
    end

    subgraph MODEL_LAYER[Model layer]
        direction LR
        MODEL[Model Interface] --> FILTER_MODEL[Filter model]
        MODEL --> SUMMARY_MODEL[Summary model]
        MODEL --> EMBEDDING[Local embedding]
        FILTER_MODEL --> PROVIDERS[Anthropic · OpenAI · Ollama]
        SUMMARY_MODEL --> PROVIDERS
        EMBEDDING --> BGE[bge-small]
    end

    STORE[(SQLite<br/>fetch progress · history · vector cache)]

    L1 --> L2 --> L3 --> L4
    L3 -. uses .-> MODEL_LAYER
    L2 -. reads / writes .-> STORE
    L3 -. reads / writes .-> STORE

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

The main path follows the content itself: frontier labs, researcher blogs, arXiv,
and Hacker News enter shared Source Adapters for concurrent fetching,
normalization, and URL deduplication before reaching the intelligence layer.

The intelligence layer applies rule filtering, LLM relevance evaluation,
semantic deduplication, topic clustering, and combined ranking before producing
P0/P1 items, summaries, the day's trend, and a reading plan.

The model layer exposes filtering, summarization, and embeddings through one
interface. The pipeline does not depend on a provider, so selecting Anthropic,
OpenAI, Ollama, or another compatible service requires no downstream changes.

The report and channel layer first produces a shared Report, then renders it
through Notifier adapters as a Lark card, webhook message, or console output.
SQLite independently stores fetch progress, history, and cached vectors. The CLI
and GitHub Actions are simply two entry points into this same processing path.

---

## Providers and delivery

`radar init` asks you to choose the provider, relevance-filter model, and summary
model during the first setup. Switching models later is a configuration change.

| Layer | Supported options |
|---|---|
| LLM | Anthropic; OpenAI; Ollama; other OpenAI-compatible endpoints; or none |
| Delivery | Lark / Feishu; Slack; Discord; Telegram; console |
| Language | Chinese (`zh`) or English (`en`) |

See [`.env.example`](./.env.example) for the available settings. Lark renders a
collapsible card; the other delivery channels use Markdown.

---

## Daily automation

Use GitHub Actions to send daily and weekly reports on schedule, with no server
of your own:

1. Fork and clone this repository.
2. Run `uv run radar init` to complete the setup.
3. Open **Settings → Secrets and variables → Actions** in your repository and
   **add each item exactly as printed at the end of `radar init`**:
   - On the **Variables** tab, click **New repository variable**, then enter its name and value.
   - On the **Secrets** tab, click **New repository secret**, then enter sensitive values such as API keys and webhooks.
   - Click **Add variable** or **Add secret** to save each item.
4. Commit and push [`.github/workflows/daily.yml`](./.github/workflows/daily.yml),
   then test it once from **Actions → Frontier Signal Daily → Run workflow**.

For example, Anthropic + Lark + English produces:

```text
GitHub Actions repository variables
  LLM_PROVIDER=anthropic
  FILTER_MODEL=claude-haiku-4-5-20251001
  SUMMARY_MODEL=claude-opus-4-8
  NOTIFIER=lark
  LANGUAGE=en

GitHub Actions repository secrets
  ANTHROPIC_API_KEY
  LARK_WEBHOOK_URL
  LARK_WEBHOOK_SECRET (optional)
```

Add both the name and value for each variable. For each secret, use the name shown
above and the secret value you entered during `radar init`.

**Variables (the first five are required)**

| Name | Example value |
|---|---|
| `LLM_PROVIDER` | `anthropic` or `openai` |
| `FILTER_MODEL` | `claude-haiku-4-5-20251001` or `gpt-4o-mini` |
| `SUMMARY_MODEL` | `claude-opus-4-8` or `gpt-4o` |
| `NOTIFIER` | `lark`, `slack`, `discord`, `telegram`, or `console` |
| `LANGUAGE` | `zh` for Chinese or `en` for English |
| `OPENAI_BASE_URL` | Only for OpenAI-compatible services; omit for OpenAI itself |

**Secrets (add only those required by your choices)**

| Use case | Name | Value |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | Anthropic API key |
| OpenAI / compatible | `OPENAI_API_KEY` | API key from the provider |
| Lark / Feishu | `LARK_WEBHOOK_URL` | Custom bot webhook URL |
| Lark signature verification (optional) | `LARK_WEBHOOK_SECRET` | Custom bot signing secret |
| Slack | `SLACK_WEBHOOK_URL` | Incoming webhook URL |
| Discord | `DISCORD_WEBHOOK_URL` | Webhook URL |
| Telegram | `TELEGRAM_BOT_TOKEN` | Token provided by @BotFather |
| Telegram | `TELEGRAM_CHAT_ID` | Chat ID that receives the report |

For example, OpenAI + Lark requires `OPENAI_API_KEY`, `LARK_WEBHOOK_URL` (plus
`LARK_WEBHOOK_SECRET` if signature verification is enabled), and the first five
Variables above. Names must match exactly; do not wrap values in quotes.

Once the test succeeds, the daily report runs automatically and the weekly report
summarizes the previous seven days. Change delivery times in
[`daily.yml`](./.github/workflows/daily.yml) and
[`weekly.yml`](./.github/workflows/weekly.yml).

> GitHub Actions cannot reach Ollama at `localhost`. Use a publicly reachable
> endpoint or a self-hosted runner.

---

## Development and extension

Install development dependencies and run the quality checks:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests use local fixtures and HTTP mocks, so they require no network access, model
API key, or webhook. After changing filtering, deduplication, ranking, or
rendering, run the full test suite and inspect real output with:

| Purpose | Command |
|---|---|
| Run an isolated preview | `uv run radar preview` |
| Inspect Markdown output | `uv run radar test-layout --channel console` |
| Inspect the Lark card | `uv run radar test-layout --channel lark` |
| Inspect source health | `uv run radar sources` |

Small interfaces isolate sources, models, and delivery channels, so extensions
remain local:

| Extension | Where to change |
|---|---|
| Add an RSS / Atom source | Add it to [`config/sources.yaml`](./config/sources.yaml) |
| Add an API source | Implement its fetcher under [`radar/sources/`](./radar/sources/) |
| Add a model provider | Implement [`LLMClient`](./radar/llm/base.py) and register it in [`factory.py`](./radar/llm/factory.py) |
| Add a delivery channel | Implement [`Notifier`](./radar/notify/base.py) and register it in [`factory.py`](./radar/notify/factory.py) |
| Add a report language | Add labels and model-writing rules in [`radar/i18n.py`](./radar/i18n.py) |
| Change filtering or ranking | Edit [`config/interests.yaml`](./config/interests.yaml) or [`radar/pipeline/`](./radar/pipeline/) |

Run the tests and Ruff checks before submitting code. See
[CONTRIBUTING.md](./CONTRIBUTING.md) for contribution guidelines.

## Configuration reference

Use `radar init` for normal setup. For finer control, edit these files or their
corresponding environment variables directly:

| Change | File |
|---|---|
| Source URLs, names, and authority weights | [`config/sources.yaml`](./config/sources.yaml) |
| Topics, keyword weights, exclusions, report size, and per-source quotas | [`config/interests.yaml`](./config/interests.yaml) |
| Providers, model names, channels, report language, and connection settings | [`.env.example`](./.env.example) |
| Relevance rubric | [`radar/pipeline/llm_filter.py`](./radar/pipeline/llm_filter.py) |
| Fixed UI text and language-specific writing rules | [`radar/i18n.py`](./radar/i18n.py) |

`.env` holds machine-specific configuration and secrets and should never be
committed. YAML files contain version-controlled content policy such as sources,
topic weights, and source quotas. Environment variables override values from
`.env`; GitHub Actions uses this mechanism to load repository Variables and
Secrets.

## Project scope

Frontier Signal focuses on AI-engineering research and production practice. Its
core job is to filter, deduplicate, rank, and summarize public material worth an
engineer's time.

It currently does not provide full-text bookmarking, read-later workflows, a team
knowledge base, feedback training, or a hosted SaaS. It is not designed to cover
all AI news: funding, policy, general technology news, and marketing are excluded
by default. New sources and features should improve signal quality or make models
and channels easier to replace, rather than merely increase volume.

Structured tracking for GitHub releases and major AI-framework releases remains
planned.

## License

[MIT](./LICENSE) © Lili Liang
