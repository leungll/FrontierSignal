# Contributing

Thanks for your interest. This project has a deliberately narrow goal — a precise
daily AI-**engineering** digest for one kind of reader — so contributions that
sharpen that focus are more welcome than ones that broaden scope.

## Dev setup

```bash
uv sync                 # install deps (including dev)
uv run radar run --dry-run   # run the pipeline without pushing
uv run pytest           # run tests (no network / API key needed)
uv run ruff check .     # lint
```

## Good contributions

- **New sources** — add to `config/sources.yaml` (RSS) or a fetcher in
  `radar/sources/` (APIs). Verify the feed is live and on-topic first.
- **A notification channel** — implement the `Notifier` protocol in
  `radar/notify/` (see `radar/notify/base.py`) and wire it into
  `radar/notify/factory.py`. Reuse `render_markdown` unless your channel has a
  richer native format worth using.
- **An LLM provider** — implement `LLMClient` in `radar/llm/` and add it to
  `radar/llm/factory.py`.
- **Better filtering / ranking** — the interest profile lives in
  `radar/pipeline/llm_filter.py`; keyword rules in `config/interests.yaml`.
- **A new language** — add a block to `radar/i18n.py` (`LABELS` + `PROMPT_LANG`).
- **Bug fixes** with a test that reproduces the bug.

## Guidelines

- Keep the report **simple and scannable**. This project values signal over
  polish; please don't add fancy formatting for its own sake.
- Prefer configuration (YAML / env) over hard-coding.
- Every non-LLM stage (dedup, scoring, clustering, quotas, typography) should stay
  unit-testable without network or API calls — see `tests/`.
- Run `ruff check .` and `pytest` before opening a PR.

## Filing issues

Include: what you ran, what you expected, what happened, and (if a source broke)
the feed URL. `radar sources` prints per-source health.
