"""CLI entrypoints. `radar run --dry-run` is the workhorse during development."""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog
import typer

from radar import db
from radar.config import REPO_ROOT, load_interests, load_sources, settings
from radar.llm import get_client
from radar.models import Item, RunStats
from radar.notify.factory import get_notifier
from radar.pipeline import cluster, embed, llm_filter, score
from radar.report import Report, WeeklyReport
from radar.sources import arxiv, hackernews, rss
from radar.summarize import digest
from radar.summarize import item as summarize_item
from radar.summarize import weekly as weekly_summary

app = typer.Typer(add_completion=False, help="Frontier Signal")


def _step(number: int, title: str) -> None:
    typer.secho(f"\n[{number}/4] {title}", fg=typer.colors.BLUE, bold=True)


def _option(name: str, description: str) -> None:
    typer.secho(f"  {name:<10}", fg=typer.colors.GREEN, bold=True, nl=False)
    typer.secho(description, dim=True)


def _hint(message: str) -> None:
    typer.secho(message, dim=True)


def _secret_prompt(label: str, *, optional: bool = False) -> str:
    _hint("Input is hidden. Paste the value, then press Enter.")
    value = typer.prompt(label, default="" if optional else None, hide_input=True)
    if value:
        typer.secho("  ✓ Saved securely (not displayed)", fg=typer.colors.GREEN)
    else:
        typer.secho("  Skipped", fg=typer.colors.YELLOW)
    return value


def _setup_logging(verbose: bool) -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if verbose else logging.INFO
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def _local_utc_offset() -> float:
    """Current UTC offset reported by the computer, including current DST."""
    offset = datetime.now().astimezone().utcoffset()
    return offset.total_seconds() / 3600 if offset else 0.0


def _cron_for(hour_local: int, tz_offset: float) -> str:
    """Daily UTC cron for a local hour and UTC offset."""
    utc_minutes = round((hour_local - tz_offset) * 60) % (24 * 60)
    utc_hour, utc_minute = divmod(utc_minutes, 60)
    return f"{utc_minute} {utc_hour} * * *"


def _weekly_cron_for(weekday: int, hour_local: int, tz_offset: float) -> str:
    """Return a UTC cron for a local weekday (Monday=0) and hour."""
    utc_minutes = round((hour_local - tz_offset) * 60)
    day_delta, minute_of_day = divmod(utc_minutes, 24 * 60)
    utc_weekday = (weekday + day_delta) % 7
    cron_weekday = (utc_weekday + 1) % 7  # cron: Sunday=0, Monday=1
    utc_hour, utc_minute = divmod(minute_of_day, 60)
    return f"{utc_minute} {utc_hour} * * {cron_weekday}"


def _write_cron(workflow: str, cron: str) -> bool:
    """Rewrite a workflow schedule so the user never hand-edits YAML."""
    import re

    wf = REPO_ROOT / ".github" / "workflows" / workflow
    if not wf.exists():
        return False
    text = wf.read_text(encoding="utf-8")
    new_text, n = re.subn(r'(-\s*cron:\s*")[^"]*(")', rf"\g<1>{cron}\g<2>", text, count=1)
    if n:
        wf.write_text(new_text, encoding="utf-8")
    return bool(n)


def _configure_schedules() -> None:
    """Prompt for local delivery times and update both workflow schedules."""
    daily_hour = -1
    while not 0 <= daily_hour <= 23:
        daily_hour = typer.prompt(
            "What hour should the daily report arrive? (0-23, in your local time)",
            default=7,
            type=int,
        )
        if not 0 <= daily_hour <= 23:
            typer.secho(
                "Enter an hour from 0 to 23. Example: 7 means 07:00, 18 means 18:00.",
                fg=typer.colors.YELLOW,
            )
    typer.secho(f"  Daily report time: {daily_hour:02d}:00", fg=typer.colors.GREEN)

    weekdays = (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    )
    weekly_day = 0
    while not 1 <= weekly_day <= 7:
        weekly_day = typer.prompt(
            "Weekly report day (1=Monday, 2=Tuesday, ..., 7=Sunday)",
            default=1,
            type=int,
        )
        if not 1 <= weekly_day <= 7:
            typer.secho("Enter a number from 1 to 7.", fg=typer.colors.YELLOW)
    weekly_hour = -1
    while not 0 <= weekly_hour <= 23:
        weekly_hour = typer.prompt(
            "What hour should the weekly report arrive? (0-23, in your local time)",
            default=8,
            type=int,
        )
        if not 0 <= weekly_hour <= 23:
            typer.secho("Enter an hour from 0 to 23.", fg=typer.colors.YELLOW)
    typer.secho(
        f"  Weekly report time: {weekdays[weekly_day - 1]} {weekly_hour:02d}:00",
        fg=typer.colors.GREEN,
    )

    detected_offset = _local_utc_offset()
    _hint(f"  Detected this computer's UTC offset: UTC{detected_offset:+g}")
    if typer.confirm("Use this timezone?", default=True):
        tz = detected_offset
    else:
        tz = typer.prompt("Enter the UTC offset", type=float)

    daily_cron = _cron_for(daily_hour, tz)
    if _write_cron("daily.yml", daily_cron):
        typer.secho(
            f"✓ Set daily schedule to {daily_hour:02d}:00 at UTC{tz:+g} "
            f'(cron "{daily_cron}") in .github/workflows/daily.yml',
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f'! Could not update .github/workflows/daily.yml (cron "{daily_cron}").',
            fg=typer.colors.YELLOW,
        )

    weekly_cron = _weekly_cron_for(weekly_day - 1, weekly_hour, tz)
    if _write_cron("weekly.yml", weekly_cron):
        typer.secho(
            f"✓ Set weekly schedule to {weekdays[weekly_day - 1]} {weekly_hour:02d}:00 at "
            f'UTC{tz:+g} (cron "{weekly_cron}") in .github/workflows/weekly.yml',
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f'! Could not update .github/workflows/weekly.yml (cron "{weekly_cron}").',
            fg=typer.colors.YELLOW,
        )


@app.command()
def schedule() -> None:
    """Configure daily and weekly GitHub Actions delivery schedules."""
    typer.secho("\nFrontier Signal", fg=typer.colors.CYAN, bold=True)
    typer.secho("Schedule", fg=typer.colors.CYAN)
    _configure_schedules()
    typer.echo("\nCommit and push the workflow changes to apply the new schedules.")


@app.command()
def init() -> None:
    """Interactive setup for provider, models, delivery, language, and schedule."""
    env_path = REPO_ROOT / ".env"
    if env_path.exists() and not typer.confirm(
        f"{env_path} already exists. Overwrite?", default=False
    ):
        typer.echo("Aborted — existing .env kept.")
        raise typer.Exit(0)

    typer.secho("\nFrontier Signal", fg=typer.colors.CYAN, bold=True)
    typer.secho("Setup", fg=typer.colors.CYAN)
    _hint("Four steps: AI models → delivery → language → schedule")
    lines: list[str] = []

    # --- LLM provider ---
    _step(1, "AI models")
    _option("anthropic", "Claude API")
    _option("openai", "OpenAI, Ollama, or another OpenAI-compatible API")
    provider = ""
    while provider not in ("anthropic", "openai"):
        provider = typer.prompt("Choose a provider").strip().lower()
    lines.append(f"LLM_PROVIDER={provider}")
    if provider == "anthropic":
        typer.secho(
            "Create or copy a key: https://console.anthropic.com/settings/keys",
            fg=typer.colors.CYAN,
        )
        key = _secret_prompt("Anthropic API key", optional=True)
        lines.append(f"ANTHROPIC_API_KEY={key}")
        filter_default = "claude-haiku-4-5-20251001"
        summary_default = "claude-opus-4-8"
    else:
        typer.secho(
            "Create or copy a key: https://platform.openai.com/api-keys",
            fg=typer.colors.CYAN,
        )
        openai_key = _secret_prompt("API key (optional for local Ollama)", optional=True)
        lines.append(f"OPENAI_API_KEY={openai_key}")
        base = typer.prompt(
            "API base URL (leave blank for OpenAI)",
            default="",
        )
        if not base:
            typer.secho("  Using https://api.openai.com/v1", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  Using {base}", fg=typer.colors.GREEN)
        lines.append(f"OPENAI_BASE_URL={base}")
        is_local = "localhost" in base or "127.0.0.1" in base
        filter_default = "llama3.1" if is_local else "gpt-4o-mini"
        summary_default = "qwen2.5" if is_local else "gpt-4o"

    _hint("Press Enter to accept the recommended models, or type another model name.")
    filter_model = typer.prompt("Fast model for filtering", default=filter_default)
    summary_model = typer.prompt("Strong model for summaries", default=summary_default)
    lines.append(f"FILTER_MODEL={filter_model}")
    lines.append(f"SUMMARY_MODEL={summary_model}")

    # --- Notification channel ---
    _step(2, "Delivery")
    _hint("Choose where reports should be sent: lark, slack, discord, telegram, or console.")
    channels = ("lark", "slack", "discord", "telegram", "console")
    channel = ""
    while channel not in channels:
        channel = typer.prompt("Delivery channel", default="lark").strip().lower()
    lines.append(f"NOTIFIER={channel}")
    if channel == "lark":
        lines.append("LARK_WEBHOOK_URL=" + _secret_prompt("Lark custom-bot webhook URL"))
        lines.append(
            "LARK_WEBHOOK_SECRET=" + _secret_prompt("Signing secret (optional)", optional=True)
        )
    elif channel == "slack":
        lines.append("SLACK_WEBHOOK_URL=" + _secret_prompt("Slack incoming-webhook URL"))
    elif channel == "discord":
        lines.append("DISCORD_WEBHOOK_URL=" + _secret_prompt("Discord webhook URL"))
    elif channel == "telegram":
        lines.append("TELEGRAM_BOT_TOKEN=" + _secret_prompt("Telegram bot token"))
        lines.append(f"TELEGRAM_CHAT_ID={typer.prompt('Telegram chat id')}")

    # --- Language + schedule ---
    _step(3, "Language")
    language = ""
    while language not in ("zh", "en"):
        language = (
            typer.prompt("Report language (zh=中文, en=English)", default="zh").strip().lower()
        )
    lines.append(f"LANGUAGE={language}")

    _step(4, "Schedule")
    _configure_schedules()

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.secho(f"\n✓ Wrote {env_path}", fg=typer.colors.GREEN, bold=True)

    typer.secho("\nNext steps", fg=typer.colors.BLUE, bold=True)
    typer.echo("  1. Test the connection:   uv run radar test-notify")
    typer.echo("  2. Preview a report:      uv run radar preview")
    typer.echo("  3. Push for real:         uv run radar run")

    typer.secho("\nGitHub Actions repository variables", fg=typer.colors.BLUE, bold=True)
    typer.echo(f"  LLM_PROVIDER={provider}")
    typer.echo(f"  FILTER_MODEL={filter_model}")
    typer.echo(f"  SUMMARY_MODEL={summary_model}")
    typer.echo(f"  NOTIFIER={channel}")
    typer.echo(f"  LANGUAGE={language}")
    if provider == "openai" and base:
        typer.echo(f"  OPENAI_BASE_URL={base}")

    secret_names = ["ANTHROPIC_API_KEY"] if provider == "anthropic" else ["OPENAI_API_KEY"]
    channel_secrets = {
        "lark": ["LARK_WEBHOOK_URL", "LARK_WEBHOOK_SECRET (optional)"],
        "slack": ["SLACK_WEBHOOK_URL"],
        "discord": ["DISCORD_WEBHOOK_URL"],
        "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
        "console": [],
    }
    secret_names.extend(channel_secrets[channel])
    if secret_names:
        typer.secho("GitHub Actions repository secrets", fg=typer.colors.BLUE, bold=True)
        for name in secret_names:
            typer.echo(f"  {name}")

    if provider == "openai" and base and ("localhost" in base or "127.0.0.1" in base):
        typer.secho(
            "\n! A GitHub-hosted runner cannot reach Ollama on your computer. "
            "Use a runner-accessible endpoint or run the schedule locally.",
            fg=typer.colors.YELLOW,
        )


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print instead of pushing to Lark"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch → score → filter → push. The daily job."""
    _setup_logging(verbose)
    log = structlog.get_logger()

    sources = load_sources()
    interests = load_interests()

    with db.connect(settings.db_path) as conn:
        db.migrate(conn)
        since = db.fetch_since(conn, settings.cold_start_days)
        run_id = db.start_run(conn)
        log.info("run.start", sources=len(sources), since=since.isoformat())

        raws, dead = asyncio.run(
            _fetch_everything(conn, sources, since),
        )

        scored = score.dedupe_in_batch(score.score_all(raws, interests, sources))

        # Cross-run dedupe: only items we've never stored are eligible.
        seen = db.known_urls(conn, [i.canonical_url for i in scored])
        fresh = [i for i in scored if i.canonical_url not in seen]
        db.insert_items(conn, fresh)

        # LLM filter: the cheap model judges relevance on the keyword-survivors.
        llm_client = get_client(settings)
        alive = [i for i in fresh if not i.is_killed]
        llm_filter.apply(
            alive,
            client=llm_client,
            model=settings.filter_model,
            max_candidates=settings.llm_max_candidates,
            trusted_authority=settings.trusted_authority,
        )

        # Embed the relevant survivors (local bge-small), then semantic-dedupe and
        # cluster. Dedup merges the same story from multiple sources; clusters feed
        # P0/P1 and the trend section. Only items Claude judged are embedded — no
        # point spending CPU on items that won't reach the report.
        judged = [i for i in alive if i.llm_relevance is not None]
        if judged:  # Claude ran — embed only items that cleared the bar
            relevant = [i for i in judged if i.llm_relevance >= settings.llm_min_relevance]
        else:  # Claude skipped/failed — fall back to keyword threshold
            relevant = [i for i in alive if i.score >= interests.min_score]
        cache = db.load_embeddings(conn, [embed.content_hash(i) for i in relevant])
        vectors = embed.embed_items(relevant, cache)
        db.save_embeddings(conn, {h: v.tolist() for h, v in vectors.items()})
        relevant = cluster.dedupe_semantic(relevant, vectors)
        cluster.cluster_topics(relevant, vectors)

        selected = score.select_for_report(
            relevant, interests, llm_min_relevance=settings.llm_min_relevance
        )

        # Split into P0 / P1 by importance (relevance + authority + coverage).
        selected = score.assign_priority(
            selected, interests.p0_count, cluster.cluster_sizes(relevant)
        )

        # The strong model summarizes only the handful that made the cut.
        summarize_item.apply(
            selected,
            client=llm_client,
            model=settings.summary_model,
            language=settings.language,
        )

        # Today's trend + 30-minute reading recommendation (one call).
        trend, reading = digest.build(
            selected,
            client=llm_client,
            model=settings.summary_model,
            language=settings.language,
        )

        stats = RunStats(
            found=len(raws),
            new=len(fresh),
            killed=sum(1 for i in fresh if i.is_killed),
            reported=len(selected),
            dead_sources=dead,
        )
        log.info(
            "run.scored",
            found=stats.found,
            new=stats.new,
            killed=stats.killed,
            reported=stats.reported,
        )

        no_report_reason = ""
        if not selected:
            log.warning("run.nothing_to_report")
            if not raws:
                no_report_reason = (
                    "No source returned new content in this time window. "
                    "Your configuration is valid; nothing was sent."
                )
            elif not fresh:
                no_report_reason = (
                    "All fetched items were already seen. "
                    "Your configuration is valid; nothing was sent."
                )
            else:
                no_report_reason = (
                    f"Fetched {len(fresh)} new items, but none met the relevance threshold. "
                    "Nothing was sent."
                )

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        report = Report(date=date_str, stats=stats, items=selected, trend=trend, reading=reading)

        if dry_run:
            typer.echo(_preview(selected, stats, date_str))
            if no_report_reason:
                typer.secho(f"\n{no_report_reason}", fg=typer.colors.YELLOW)
        elif no_report_reason:
            typer.secho(no_report_reason, fg=typer.colors.YELLOW)
        else:
            get_notifier(settings).send(report, language=settings.language)
            db.mark_reported(conn, [i.canonical_url for i in selected])

        db.finish_run(
            conn, run_id, ok=True, found=stats.found, new=stats.new, reported=stats.reported
        )
        log.info("run.done")


@app.command()
def preview() -> None:
    """Run a clean one-time preview without changing the normal radar state."""
    original_db_path = settings.db_path
    with tempfile.TemporaryDirectory(prefix="frontier-signal-preview-") as temp_dir:
        settings.db_path = Path(temp_dir) / "radar.db"
        try:
            run(dry_run=True, verbose=False)
        finally:
            settings.db_path = original_db_path


async def _fetch_everything(conn, sources, since):
    """Run RSS + arXiv + HN concurrently. arXiv/HN failures are isolated (they
    return [] on error) and don't count toward dead_sources."""
    import httpx

    raws, dead = await rss.fetch_all(
        conn,
        sources,
        since=since,
        timeout=settings.http_timeout,
        concurrency=settings.max_concurrency,
        cap=settings.per_source_cap,
    )

    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": rss.USER_AGENT},
        follow_redirects=True,
    ) as client:
        api_results = await asyncio.gather(
            arxiv.fetch(client, since=since, cap=settings.per_source_cap),
            hackernews.fetch(client, since=since, cap=settings.per_source_cap),
        )
    for result in api_results:
        raws.extend(result)

    return raws, dead


def _preview(items, stats, date_str: str) -> str:
    lines = [
        f"\n=== AI Engineering Daily · {date_str} ===",
        f"Found {stats.found} · new {stats.new} · killed {stats.killed} "
        f"· reported {stats.reported}",
        "",
    ]
    for i, item in enumerate(items, 1):
        rel = (
            f"rel {item.llm_relevance}/10"
            if item.llm_relevance is not None
            else f"kw {item.score:g}"
        )
        lines.append(f"{i}. [{rel}] {item.title}")
        lines.append(f"   {item.source_name} · {item.url}")
        if item.summary.strip():
            lines.append(f"   {item.summary.strip()[:160]}")
        if item.why_it_matters.strip():
            lines.append(f"   → {item.why_it_matters.strip()[:160]}")
    if stats.dead_sources:
        lines.append(f"\nDead sources: {', '.join(stats.dead_sources)}")
    return "\n".join(lines)


def _layout_sample(language: str) -> Report:
    """Deterministic sample content for checking channel rendering."""
    if language == "zh":
        content = [
            (
                "Agentic Configuration Management：面向 Agent 系统的配置治理模型",
                "arXiv",
                9,
                "ACM 提出一个与框架无关的配置治理参考模型，将 agent、prompt、tool、model、policy 与 workflow 等异构组件统一表示为可版本化、可追踪的配置对象。",
                "现有 AgentOps 更多解决编排与可观测性，却缺少统一的配置治理层。ACM 为生产系统提供审计、回滚和合规边界，补上了 agent 从 demo 走向长期维护的关键一环。",
            ),
            (
                "Long-Horizon AI Research for the Grothendieck Constant",
                "arXiv",
                9,
                "这项案例研究记录研究者如何使用 long-horizon AI research system 与人类数学家协作，持续提出猜想、检索证据并收紧 Grothendieck 常数的已知上下界。",
                "它展示的不是一次性解题，而是一套可持续数天甚至数周的研究循环：拆解问题、调用工具、验证中间结论并保留研究记忆，对研究型 agent 的真实落地很有参考价值。",
            ),
            (
                "Actions Speak Louder than Words：跨语言 Tool-Using Agent Eval",
                "arXiv",
                8,
                "研究覆盖 8 个模型、6 个并行 benchmark、41 种语言和 238 万次 rollout，衡量 tool-using agent 在切换语言后是否仍会采取一致的 action policy，而不只比较最终答案。",
                "最终答案相似并不代表执行过程稳定。action 层面的偏移会直接改变工具成本、延迟与失败模式，这套评测方法为多语言 agent 的上线验收提供了更可信的观察尺度。",
            ),
            (
                "Introducing Muse Glimmer：面向端到端 Agent 任务的开放模型",
                "Simon Willison",
                7,
                "Meta 发布 Muse Glimmer，一个采用 Apache 2.0 许可的 30B open-weights 模型，针对检索、工具调用、规划与代码修改等端到端 agentic 任务进行优化。",
                "它在 DeepSearch QA、MCP-Atlas、τ-Bench 与 SWE-Bench 等任务上的表现，使其成为本地部署 agent 的新候选；宽松许可也降低了企业评估和二次开发的阻力。",
            ),
            (
                "使用 NVIDIA Magpie TTS 构建低延迟多语言 Voice Agent",
                "Hugging Face",
                7,
                "文章给出一套基于 NVIDIA Magpie TTS 的完整方案，用 open weights 构建低延迟、多语言、可自行部署的语音 agent，并讨论流式生成与服务端推理配置。",
                "对于不能把语音数据交给闭源 API，或对首包延迟和部署区域有严格要求的团队，这提供了一条可控制模型、数据和 serving 栈的工程路径。",
            ),
            (
                "CARE-X：结合工具测量与奖励对齐的临床放射 VLM",
                "Microsoft Research",
                7,
                "CARE-X 面向胸片解读，将辅助监督、reward-aligned learning 和 tool-augmented measurement 结合起来，让模型在生成报告之外还能调用测量工具并输出可校准判断。",
                "这项工作把医疗 VLM 从纯文本描述推进到可量化的临床决策支持，也说明高风险 agent 需要把外部工具、置信度与验证流程纳入同一条推理链。",
            ),
            (
                "Advancing AMIE：走向专家级音视频临床问诊",
                "Google Research",
                6,
                "Google 继续推进 AMIE，使其能够在远程问诊中综合患者语言、声音线索和可见症状，完成多轮信息收集、鉴别诊断与后续建议。",
                "真实问诊远不止文本问答。音频和视觉输入进入诊断 agent 后，模型必须处理信息缺失、模态冲突与安全升级，这对多模态 agent 的评测和产品设计都有直接启发。",
            ),
            (
                "Lean Eval for Alignment on Faithfulness",
                "Hacker News",
                6,
                "该项目尝试使用 Lean 形式化系统表达 alignment 中的 faithfulness 属性，并让模型生成能够被机器检查的证明或反例，而不是只依赖经验性 benchmark 分数。",
                "形式化验证不能覆盖全部开放式行为，但它能为关键性质提供确定性检查。对 eval 工程而言，这是从统计观察走向可验证约束的一条值得关注的路线。",
            ),
        ]
        trend = (
            "今天的主线是 agent 从能力展示走向可治理、可度量、可部署的工程系统。"
            "#1 把 agent、prompt、tool 与 policy 纳入统一配置治理，#3 用大规模 rollout 观察跨语言的 action policy，"
            "#8 则尝试用形式化方法验证 faithfulness：可靠性评价正在从“答案是否正确”扩展到“行为是否稳定、过程是否可审计”。"
            "与此同时，#5 的自托管语音栈以及 #6、#7 的临床多模态系统表明，模型能力只有进入明确的数据、延迟与安全边界，才真正具备生产价值。"
        )
        reading = (
            "#3 — 先看跨语言 agent 的 238 万次 rollout，评测方法对 tool-use 系统有长期参考价值。\n"
            "#1 — 再看框架无关的配置治理模型，理解生产 agent 的审计与回滚边界。\n"
            "#8 — 最后看 Lean 如何介入 faithfulness eval，补充一条可验证的可靠性路线。"
        )
    else:
        content = [
            (
                "Agentic Configuration Management for Governed Agent Systems",
                "arXiv",
                9,
                "ACM proposes a framework-agnostic reference model that represents agents, prompts, tools, models, policies, and workflows as versioned and traceable configuration objects.",
                "AgentOps platforms cover orchestration and observability, but often lack a shared governance layer. Audit trails, rollback, and compliance boundaries are essential when agent systems move into long-lived production environments.",
            ),
            (
                "Long-Horizon AI Research for the Grothendieck Constant",
                "arXiv",
                9,
                "This case study follows a long-horizon AI research system working with mathematicians to generate conjectures, retrieve evidence, and tighten known bounds on the Grothendieck constant.",
                "The system demonstrates a sustained research loop rather than one-shot theorem solving: decomposing problems, using tools, checking intermediate results, and preserving research memory over an extended investigation.",
            ),
            (
                "Actions Speak Louder than Words: Cross-Lingual Tool-Using Agent Evals",
                "arXiv",
                8,
                "Across 8 models, 6 parallel benchmarks, 41 languages, and 2.38 million rollouts, the study measures whether tool-using agents retain the same action policy when the task language changes.",
                "Equivalent final answers can hide materially different execution paths. Action-level drift changes tool cost, latency, and failure modes, making trajectory analysis a stronger acceptance test for multilingual agents.",
            ),
            (
                "Introducing Muse Glimmer for End-to-End Agentic Tasks",
                "Simon Willison",
                7,
                "Meta introduces Muse Glimmer, a 30B open-weight model under Apache 2.0, optimized for end-to-end agentic work spanning retrieval, planning, tool use, and code modification.",
                "Its results on DeepSearch QA, MCP-Atlas, tau-Bench, and SWE-Bench make it a credible candidate for locally deployed agents, while the permissive license lowers the cost of evaluation and customization.",
            ),
            (
                "Building Low-Latency Multilingual Voice Agents with NVIDIA Magpie TTS",
                "Hugging Face",
                7,
                "A practical deployment guide for building low-latency multilingual voice agents with NVIDIA Magpie TTS, open weights, streaming generation, and full control of the serving stack.",
                "Teams with strict voice-data, latency, or regional deployment requirements get an alternative to closed APIs without giving up control over models, infrastructure, and runtime behavior.",
            ),
            (
                "CARE-X: Tool-Augmented and Reward-Aligned Radiology VLMs",
                "Microsoft Research",
                7,
                "CARE-X combines auxiliary supervision, reward-aligned learning, and tool-augmented measurement so a chest-radiography VLM can produce reports alongside calibrated, measurable findings.",
                "The work moves medical VLMs beyond free-form report generation and shows how external tools, confidence estimates, and verification can coexist in a high-stakes reasoning pipeline.",
            ),
            (
                "Advancing AMIE toward Expert-Level Audio-Visual Consultations",
                "Google Research",
                6,
                "AMIE is extended to combine conversation, vocal signals, and visible symptoms during remote clinical consultations while conducting information gathering, differential diagnosis, and follow-up guidance.",
                "Real consultations are not text-only. Adding audio and vision introduces missing evidence, modality conflicts, and escalation requirements that directly inform multimodal-agent evaluation and product safety.",
            ),
            (
                "Lean Eval for Alignment on Faithfulness",
                "Hacker News",
                6,
                "The project uses the Lean theorem prover to express faithfulness properties and asks models to produce machine-checkable proofs or counterexamples rather than relying only on empirical benchmark scores.",
                "Formal methods cannot cover every open-ended behavior, but they can provide deterministic checks for critical properties and point toward a verifiable layer in alignment evaluation.",
            ),
        ]
        trend = (
            "Today's common thread is the shift from impressive agent capabilities to systems that can be governed, measured, and deployed. "
            "#1 treats agents, prompts, tools, and policies as auditable configuration; #3 measures action-policy stability across languages; and #8 brings formal verification into faithfulness evaluation. "
            "Together they suggest that reliability is expanding beyond answer accuracy to include behavioral stability and inspectable execution. Meanwhile, self-hosted voice infrastructure and clinical multimodal agents show that model capability becomes useful only inside explicit data, latency, and safety boundaries."
        )
        reading = (
            "#3 — Start with the 2.38 million-rollout study; its trajectory method has lasting value for tool-use evaluation.\n"
            "#1 — Continue with the framework-neutral governance model and its audit and rollback boundaries.\n"
            "#8 — Finish with Lean-based faithfulness evaluation as a complementary path toward verifiable reliability."
        )

    items = []
    for index, (title, source, relevance, summary, why) in enumerate(content, 1):
        items.append(
            Item(
                source_id=f"sample-{index}",
                source_name=source,
                title=title,
                url=f"https://example.com/frontier-signal/{index}",
                summary=summary,
                canonical_url=f"https://example.com/frontier-signal/{index}",
                llm_relevance=relevance,
                why_it_matters=why,
                priority="P0" if index <= 2 else "P1",
            )
        )
    return Report(
        date="排版预览 · 示例内容" if language == "zh" else "Layout preview · sample content",
        stats=RunStats(found=131, new=131, reported=8),
        items=items,
        trend=trend,
        reading=reading,
    )


@app.command()
def sources() -> None:
    """List configured sources and their health."""
    _setup_logging(False)
    with db.connect(settings.db_path) as conn:
        db.migrate(conn)
        for src in load_sources():
            state = db.get_source_state(conn, src.id)
            if state is None:
                status = "never fetched"
            elif state["consecutive_failures"] > 0:
                status = f"FAILING x{state['consecutive_failures']}: {state['last_error']}"
            else:
                status = f"ok (last {state['last_fetch_at']})"
            typer.echo(f"{src.id:14} auth={src.authority:<4} {status}")


@app.command()
def weekly(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print instead of pushing"),
    days: int = typer.Option(7, help="Look-back window"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Weekly deep dive — synthesize the last 7 days of reported items. Sundays."""
    _setup_logging(verbose)
    log = structlog.get_logger()

    with db.connect(settings.db_path) as conn:
        db.migrate(conn)
        rows = db.reported_since(conn, days)
        log.info("weekly.start", items=len(rows), days=days)

        sections = weekly_summary.build(
            rows,
            client=get_client(settings),
            model=settings.summary_model,
            language=settings.language,
        )
        if sections is None:
            log.warning("weekly.nothing")
            typer.echo("Not enough material for a weekly report.")
            raise typer.Exit(0)

        section_order = weekly_summary.sections(settings.language)
        date_str = datetime.now(UTC).strftime("Week of %Y-%m-%d")
        report = WeeklyReport(date=date_str, sections=sections, order=section_order)

        if dry_run:
            for key, heading in section_order:
                if sections.get(key, "").strip():
                    typer.echo(f"\n{heading}\n{sections[key].strip()}")
        else:
            get_notifier(settings).send_weekly(report, language=settings.language)
        log.info("weekly.done")


@app.command(name="test-notify")
def test_notify() -> None:
    """Send a clear connection-success message through the configured notifier."""
    _setup_logging(True)
    get_notifier(settings).send_test(language=settings.language)
    typer.secho("Connection test sent successfully ✓", fg=typer.colors.GREEN)


def _channel_configured(channel: str) -> bool:
    checks = {
        "lark": bool(settings.lark_webhook_url),
        "slack": bool(settings.slack_webhook_url),
        "discord": bool(settings.discord_webhook_url),
        "telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "console": True,
    }
    return checks[channel]


@app.command(name="test-layout")
def test_layout(
    channel: str | None = typer.Option(
        None,
        "--channel",
        "-c",
        help="lark, slack, discord, telegram, console, or all",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        "-l",
        help="Sample language: zh or en (defaults to configured language)",
    ),
) -> None:
    """Send a fixed sample report to inspect channel-specific layout."""
    _setup_logging(False)
    selected_channel = (channel or settings.notifier or "console").lower()
    selected_language = (language or settings.language or "zh").lower()
    valid_channels = ("lark", "slack", "discord", "telegram", "console")

    if selected_channel not in (*valid_channels, "all"):
        raise typer.BadParameter("channel must be lark, slack, discord, telegram, console, or all")
    if selected_language not in ("zh", "en"):
        raise typer.BadParameter("language must be zh or en")

    targets = valid_channels if selected_channel == "all" else (selected_channel,)
    sent = 0
    for target in targets:
        if not _channel_configured(target):
            message = f"Skipped {target}: credentials are not configured in .env"
            if selected_channel == "all":
                typer.secho(message, fg=typer.colors.YELLOW)
                continue
            raise typer.BadParameter(message)

        channel_settings = settings.model_copy(update={"notifier": target})
        get_notifier(channel_settings).send(
            _layout_sample(selected_language), language=selected_language
        )
        typer.secho(f"Layout sample sent to {target} ✓", fg=typer.colors.GREEN)
        sent += 1

    if not sent:
        raise typer.Exit(1)


@app.command(name="test-lark", hidden=True)
def _test_lark_alias() -> None:
    """Deprecated alias for `test-notify`."""
    test_notify()


if __name__ == "__main__":
    app()
