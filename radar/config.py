"""Config loading: YAML for sources/interests, env for secrets."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from radar.models import Interests, Source

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    """Secrets and runtime knobs. Read from env or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Notification channel: lark | slack | discord | telegram | console.
    # "console" prints to stdout (zero config) — the default for a first run.
    notifier: str = "lark"

    lark_webhook_url: str = ""
    lark_webhook_secret: str = ""
    slack_webhook_url: str = ""
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Report language: "zh" (Chinese with English technical terms) or "en".
    language: str = "zh"

    # LLM provider: "anthropic" (Claude) or "openai" (OpenAI / Ollama / any
    # OpenAI-compatible endpoint via openai_base_url).
    llm_provider: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""  # e.g. http://localhost:11434/v1 for Ollama

    # Two-tier by design: a cheap model triages relevance, a strong one writes the
    # summaries. `radar init` sets both for the selected provider.
    filter_model: str = ""
    summary_model: str = ""
    # Below this LLM relevance score (0-10) an item is dropped from the report.
    llm_min_relevance: int = 6
    # Cap how many keyword-survivors we spend Claude tokens on per run.
    llm_max_candidates: int = 40
    # Sources at/above this authority get a "fast lane": every post is judged by
    # Claude regardless of keyword score, so landmark posts aren't buried. 0.9
    # covers Anthropic/OpenAI/DeepMind/Lilian Weng in the default sources.yaml.
    trusted_authority: float = 0.9

    db_path: Path = DATA_DIR / "radar.db"
    # Fetch window used only on a cold DB; afterwards it derives from
    # last_successful_run so a missed run self-heals rather than leaving a gap.
    cold_start_days: int = 3
    http_timeout: float = 20.0
    max_concurrency: int = 8
    per_source_cap: int = 50


def load_sources(path: Path | None = None) -> list[Source]:
    path = path or CONFIG_DIR / "sources.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Source(**s) for s in raw["sources"]]


def load_interests(path: Path | None = None) -> Interests:
    path = path or CONFIG_DIR / "interests.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Interests(**raw)


settings = Settings()
