# Frontier Signal — System Design

A personal, daily AI engineering intelligence pipeline. Runs on GitHub Actions,
stores state in SQLite, filters ~50 candidate items down to ~5 worth reading, and
pushes a Lark card every morning.

**Design decisions locked in:** GitHub Actions + SQLite · tiered LLM filtering
(rules → Haiku triage → Opus deep pass) · skimmable Lark card + full report link.

---

## 1. Guiding principle

The hard problem is **not** collecting content — RSS makes that trivial. The hard
problem is **throwing 90% of it away without discarding the 10% that matters.**

Everything below is organized around that. The filtering pipeline is the product;
the crawlers and the Lark formatting are plumbing.

A second principle worth stating: **the archive is the moat.** Every daily run
appends structured records to SQLite. After a month you have a corpus that makes
weekly synthesis, trend detection, and "is this actually new?" checks possible.
A system that only pushes and forgets can never do trend detection.

---

## 2. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Your preference; best ecosystem for this |
| Package manager | **uv** | 10-100x faster than pip; single-file lockfile. Not installed on your machine yet — `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Feed parsing | **feedparser** | Handles RSS/Atom/malformed feeds. 20 years of edge cases solved |
| HTTP | **httpx** | async + HTTP/2 + timeouts that actually work |
| HTML → text | **trafilatura** | Best-in-class boilerplate removal. Beats readability-lxml on benchmarks |
| Retries | **tenacity** | Declarative backoff decorators |
| Embeddings | **sentence-transformers** (`BAAI/bge-small-en-v1.5`) | Runs locally, free, 384-dim, ~130MB. Anthropic has no embeddings endpoint |
| Vector math | **numpy** + SQLite BLOB | 50 items/day does not need a vector DB. Brute-force cosine over ~5k vectors is <10ms |
| Clustering | **scikit-learn** (AgglomerativeClustering) | Correct algorithm for unknown cluster count |
| LLM | **anthropic** SDK | Haiku 4.5 triage + Opus 5 synthesis |
| Data models | **pydantic v2** | Runtime validation at every pipeline stage boundary |
| DB | **SQLite** (stdlib `sqlite3`) | Zero-config, file-based, commits to git |
| Migrations | plain SQL files + `user_version` | An ORM is overkill for 6 tables |
| Config | **YAML** (sources) + **pydantic-settings** (secrets) | Sources change often, code shouldn't |
| CLI | **typer** | `radar run --dry-run` beats editing `if __name__` blocks |
| Logging | **structlog** | JSON logs; greppable in Actions output |
| Testing | **pytest** + **respx** | respx mocks httpx cleanly |
| Lint/format | **ruff** | Replaces black+isort+flake8, one tool |

**Deliberately rejected:**

- **LangChain / LlamaIndex** — this pipeline is ~6 sequential stages with explicit
  control flow. A framework adds indirection and version churn for zero benefit.
  Call the SDK directly.
- **Postgres/pgvector, Qdrant, Chroma** — at 50 items/day you'd hit 18k vectors
  after a year. `numpy.dot` handles that in milliseconds.
- **Celery / Airflow** — one daily job. `cron` is the scheduler.
- **Docker** — GitHub Actions gives you a clean Ubuntu box. Container adds build time.

---

## 3. Project architecture

```
frontier-signal/
├── .github/workflows/
│   ├── daily.yml               # cron 23:00 UTC (07:00 CST) → daily report
│   ├── weekly.yml              # cron Sun 00:00 UTC → weekly deep dive
│   └── ci.yml                  # ruff + pytest on PR
├── config/
│   ├── sources.yaml            # every feed/API/repo — the file you edit weekly
│   ├── interests.yaml          # your topic taxonomy + weights + kill-list
│   └── prompts/                # LLM prompts as versioned .md files, not strings
│       ├── triage.md
│       ├── deep_read.md
│       ├── daily_synthesis.md
│       └── weekly_synthesis.md
├── radar/
│   ├── models.py               # pydantic: Item, ScoredItem, Cluster, Report
│   ├── db.py                   # connection, migrations, queries
│   ├── sources/
│   │   ├── base.py             # Source protocol: fetch() -> list[RawItem]
│   │   ├── rss.py              # generic RSS/Atom (covers ~80% of sources)
│   │   ├── arxiv.py            # arXiv Atom API
│   │   ├── github.py           # GitHub REST: trending + releases
│   │   ├── hackernews.py       # Algolia HN API
│   │   └── reddit.py           # Reddit JSON API
│   ├── pipeline/
│   │   ├── fetch.py            # stage 1: concurrent fetch, per-source isolation
│   │   ├── dedupe.py           # stage 2: URL canon → hash → embedding
│   │   ├── prefilter.py        # stage 3: rules + interest-vector scoring
│   │   ├── triage.py           # stage 4: Haiku batch scoring
│   │   ├── extract.py          # stage 5: full-text fetch for survivors only
│   │   ├── cluster.py          # stage 6: agglomerative on embeddings
│   │   ├── deep_read.py        # stage 7: Opus summaries for finalists
│   │   └── synthesize.py       # stage 8: trend + reading recommendation
│   ├── render/
│   │   ├── markdown.py         # full report → docs/archive/YYYY-MM-DD.md
│   │   └── lark_card.py        # Card JSON v2 builder, 20KB-aware
│   ├── notify/lark.py          # webhook client w/ HMAC signing + retry
│   ├── memory.py               # seen-topics, trend state, feedback loop
│   └── cli.py                  # typer entrypoints
├── data/
│   └── radar.db                # SQLite — committed to a `data` branch
├── docs/
│   ├── design.md               # this file
│   └── archive/YYYY-MM-DD.md   # full reports, served via GitHub Pages
└── tests/
```

**The one architectural rule:** each pipeline stage is a pure function
`(list[T], Context) -> list[U]`. No stage reaches into the DB or calls the network
except through injected clients. This is what makes `radar run --dry-run --from-cache`
possible, and it's what makes the whole thing testable without hitting the network.

---

## 4. Scheduling

```yaml
# .github/workflows/daily.yml
on:
  schedule:
    - cron: '0 23 * * *'      # 23:00 UTC = 07:00 next-day CST
  workflow_dispatch:           # manual trigger — you will use this constantly
```

Three things to know about Actions cron:

1. **Jitter is real.** Scheduled runs can be delayed 5–15 minutes under load, and
   occasionally skipped entirely during GitHub incidents. For a morning reading
   digest this is fine. Schedule for 23:00 UTC so a 15-min delay still lands before
   you wake up.
2. **Cron on the default branch only.** Schedule triggers read the workflow file
   from the default branch, regardless of what other branches contain.
3. **60-day inactivity disable.** If the repo has no commits for 60 days, scheduled
   workflows are auto-disabled. Since each run commits the DB, this never triggers.

**State persistence.** The honest tradeoff: committing a binary SQLite file to git
grows history. Mitigation — a dedicated orphan `data` branch, force-pushed each run
so history stays at depth 1:

```yaml
- name: Persist state
  run: |
    git checkout --orphan data-tmp
    git add -f data/radar.db docs/archive/
    git -c user.email=bot@radar -c user.name=radar commit -m "state $(date -I)"
    git push --force origin data-tmp:data
```

The DB stays ~5MB after a year (text is compressible, embeddings are the bulk).
If it ever exceeds ~50MB, switch to a GitHub Release asset or Cloudflare R2 —
one-line change in `db.py`.

---

## 5. Crawling: RSS vs APIs

**Decision rule: use an API when it gives you signal RSS can't. Otherwise RSS.**

APIs give structured metadata (star counts, upvotes, categories) that feeds the
scorer. RSS gives you titles and links. For a *ranking* system, that metadata is
the whole point — so use APIs for anything with engagement signal.

| Source | Mechanism | Endpoint / notes |
|---|---|---|
| OpenAI Blog / Research | RSS | `https://openai.com/blog/rss.xml` |
| Anthropic News / Engineering | RSS | `https://www.anthropic.com/rss.xml` |
| Google DeepMind | RSS | `https://deepmind.google/blog/rss.xml` |
| Google AI Blog | RSS | `https://blog.google/technology/ai/rss/` |
| Meta AI | RSS | `https://ai.meta.com/blog/rss/` |
| Mistral | RSS | `https://mistral.ai/news/rss.xml` |
| Hugging Face Blog | RSS | `https://huggingface.co/blog/feed.xml` — **verified: title+link only, no description** |
| Lilian Weng | RSS | `https://lilianweng.github.io/index.xml` |
| Chip Huyen | RSS | `https://huyenchip.com/feed.xml` |
| Sebastian Raschka | RSS | `https://magazine.sebastianraschka.com/feed` (Substack) |
| Jay Alammar | RSS | `https://jalammar.github.io/feed.xml` |
| Latent Space | RSS | `https://www.latent.space/feed` |
| The Gradient | RSS | `https://thegradient.pub/rss/` |
| arXiv | **API** | Atom API — see §6 |
| GitHub trending | **API** | Search API — see §7 |
| GitHub releases | **API** | `/repos/{o}/{r}/releases/latest` for a watchlist |
| Hacker News | **API** | Algolia: `https://hn.algolia.com/api/v1/search_by_date` |
| Reddit | **API** | `https://reddit.com/r/{sub}/top.json?t=day` |

**Papers With Code** — the site was sunset in mid-2025 and redirects; its data
lives on in the HF `paperswithcode` mirrors. Treat arXiv + HF Daily Papers
(`https://huggingface.co/papers`) as the replacement. Don't build against a dead API.

**Crawling discipline** — this is a bot hitting other people's servers; behave:

- Real User-Agent: `AI-Research-Radar/1.0 (+https://github.com/<you>/ai-research-radar)`
- **Conditional requests**: store `ETag`/`Last-Modified` per source, send
  `If-None-Match`/`If-Modified-Since`. A 304 costs you nothing and saves them bandwidth.
  This alone cuts fetch time ~70% on a typical day.
- Respect `robots.txt` for full-text extraction (`urllib.robotparser`)
- Concurrency capped at 8 (`asyncio.Semaphore`), per-host serialized
- `tenacity` retry: 3 attempts, exponential backoff, only on 5xx/timeout — **never on 4xx**
- **Per-source failure isolation**: one dead feed must never kill a run. Wrap each
  source in try/except, log the failure, carry on. Report dead sources in the card
  footer so you know to fix them.

---

## 6. arXiv integration

Verified constraints from the official API manual:

- **3-second delay between requests** (their explicit recommendation)
- `max_results` ≤ 2000/request; 30,000 total ceiling
- Date filter format: `submittedDate:[YYYYMMDDHHMM+TO+YYYYMMDDHHMM]` (GMT)
- Bulk harvesting should use OAI-PMH, not this API — we're not bulk, so the API is right

```python
CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.DC", "cs.SE", "cs.MA"]
# cs.DC = distributed computing → your "Distributed Systems for AI" interest
# cs.MA = multi-agent → your "AI Agents" interest
# cs.SE = software engineering → "AI Coding"

query = (
    "(" + "+OR+".join(f"cat:{c}" for c in CATEGORIES) + ")"
    f"+AND+submittedDate:[{since:%Y%m%d%H%M}+TO+{now:%Y%m%d%H%M}]"
)
```

**The volume problem.** cs.AI + cs.LG + cs.CL alone produce **500–800 papers/day**.
Sending all of them to an LLM is both expensive and pointless. Three-stage funnel:

1. **Embedding prefilter (free).** Cosine similarity of title+abstract against your
   interest vector (§10). Keep top 40. This is where 95% of the reduction happens.
2. **Haiku triage (~$0.01).** Score those 40 on novelty + engineering relevance.
   Keep top 8.
3. **Opus deep read.** Only the finalists that survive cross-source ranking.

**Prefer the abstract over the PDF.** arXiv abstracts are dense and self-describing;
parsing PDFs adds latency, cost, and failure modes for marginal signal gain. If a
paper survives to P0, link the PDF and let the human read it.

---

## 7. GitHub integration

**GitHub has no trending API.** The `/trending` page is HTML-only, and scrapers
for it break constantly. Use the Search API to construct trending yourself — it's
more stable *and* more tunable:

```python
# New/fast-rising AI repos
q = "topic:llm OR topic:ai-agents OR topic:rag created:>2026-06-01 stars:>150"
GET /search/repositories?q={q}&sort=stars&order=desc

# Rate limits: 30 req/min authenticated for search (10/min unauthenticated).
# Core API: 5000 req/hr authenticated. Always use GITHUB_TOKEN.
```

**Star velocity beats star count.** A repo going 200→900 stars in a week matters
more than a 40k-star repo that gained 50. You get this free from the archive:

```sql
-- stars_delta_7d, computed from your own history
SELECT repo, stars - LAG(stars, 7) OVER (PARTITION BY repo ORDER BY day) AS velocity
FROM repo_snapshots;
```

This is a concrete example of why the archive matters — velocity is **impossible**
without your own historical snapshots, and it's the single best GitHub signal.

**Release watching.** Maintain a watchlist in `sources.yaml` and poll
`/releases/latest`. Framework releases are high-signal for a senior engineer:

```yaml
github_releases:
  - vllm-project/vllm
  - sgl-project/sglang
  - anthropics/anthropic-sdk-python
  - modelcontextprotocol/servers
  - langchain-ai/langgraph
  - BerriAI/litellm
  - ray-project/ray
  - triton-lang/triton
```

Filter to minor+ versions — patch releases are noise.

---

## 8. Deduplication

Three layers, cheapest first. This ordering matters: never pay for an embedding
comparison when a hash would do.

**Layer 1 — URL canonicalization (free).**

```python
def canonicalize(url: str) -> str:
    u = urlparse(url.lower().rstrip("/"))
    # strip utm_*, ref, fbclid, gclid, source
    qs = {k: v for k, v in parse_qsl(u.query)
          if not (k.startswith("utm_") or k in TRACKING_PARAMS)}
    host = u.netloc.removeprefix("www.")
    # arXiv: abs/2601.12345v3 and pdf/2601.12345v1 → arxiv:2601.12345
    if "arxiv.org" in host:
        return f"arxiv:{ARXIV_ID_RE.search(u.path).group(1)}"
    return urlunparse(("https", host, u.path, "", urlencode(sorted(qs.items())), ""))
```

`UNIQUE` index on `canonical_url`. Catches the majority of dupes for free.

**Layer 2 — title SimHash (near-free).** Catches the same story syndicated under
slightly different headlines. 64-bit SimHash over title trigrams; Hamming distance
≤ 3 → duplicate. Pure-Python, microseconds.

**Layer 3 — embedding similarity (cheap).** Cosine ≥ 0.92 against items from the
last 14 days → duplicate. This catches the genuinely hard case: an arXiv paper, its
HN discussion, a Twitter-thread writeup, and the author's blog post are four URLs
and four titles for **one** thing.

**Merge, don't discard.** When items collide, keep the highest-authority source as
canonical and attach the others as `related_urls`. The HN thread is valuable
context ("340 comments") even when the arXiv link is canonical — and the fact that
something appeared in 4 places is itself a strong importance signal. Feed
`source_count` into the scorer.

**Cross-day dedupe** is why the DB matters. Check every new item against a 30-day
window, not just today's batch. Otherwise a slow-burn story reappears daily.

---

## 9. Topic clustering

Clustering runs **after** prefiltering (on ~25 items, not 500) — it's for grouping
the survivors into report sections, not for reducing volume.

```python
from sklearn.cluster import AgglomerativeClustering

clusterer = AgglomerativeClustering(
    n_clusters=None,              # unknown ahead of time — this is the key setting
    distance_threshold=0.35,      # 1 - cosine_similarity
    metric="cosine",
    linkage="average",
)
labels = clusterer.fit_predict(embeddings)
```

**Why agglomerative and not k-means:** you don't know how many topics a given day
contains. Some days have one dominant story; some have six unrelated ones. k-means
forces a fixed `k`; HDBSCAN discards outliers as noise — and a lone outlier is often
the most interesting item of the day. Agglomerative with a distance threshold
handles variable cluster counts and keeps singletons.

Cluster labels come from Opus in the same call that writes summaries — no separate
labeling pass.

---

## 10. Importance scoring

This is the heart of the system. **Hybrid: cheap deterministic signal gates the
expensive LLM judgment.**

### Stage A — rule + embedding prefilter (free, ~50 → ~25)

```
score = 0.40 * interest_similarity     # cosine vs. your interest vector
      + 0.20 * source_authority        # hand-tuned per source, config-driven
      + 0.15 * engagement_norm         # log(HN points), log(stars), log(upvotes)
      + 0.15 * novelty                 # 1 - max_sim(last 30 days) — is this NEW?
      + 0.10 * recency_decay           # exp(-hours/48)
      - 1.00 * kill_list_hit           # hard veto
```

**`interest_similarity`** — build once, reuse daily. Embed each phrase from your
interests list (AI Agents, Agent Runtime, LLM Serving, Inference, Evals, Context
Engineering, Memory, MCP, Tool Calling, AI Systems, …), then take the **max**
similarity across topics rather than the mean. Mean punishes an item that's a
perfect match for exactly one topic — which is precisely what you want to surface.

**`novelty`** is the anti-echo-chamber term, and it directly serves your brief.
"Another RAG tutorial" scores near-zero because it's ~0.95 similar to 200 things
already in your DB. A genuinely new technique has no close neighbor and scores high.
Without an archive you cannot compute this at all.

**`kill_list`** — a hard veto from `interests.yaml`, encoding your stated
non-interests:

```yaml
kill_list:
  title_patterns:
    - "(?i)\\braises?\\b.*\\b(series [a-e]|seed|\\$\\d+[MB])\\b"
    - "(?i)\\b(valuation|IPO|acquires?|acquisition)\\b"
    - "(?i)\\b(top \\d+|best \\d+|ultimate guide|you should know)\\b"
    - "(?i)\\bAI (regulation|policy|bill|act|executive order)\\b"
  domains: [techcrunch.com, venturebeat.com, businessinsider.com]
```

### Stage B — Haiku triage (~$0.01/day, ~25 → ~8)

One batched call. All 25 items as a numbered list, structured output back:

```python
class Triage(BaseModel):
    id: int
    keep: bool
    importance: int = Field(ge=0, le=100)
    category: Literal["research","engineering","tooling","production","noise"]
    one_line: str
    reason: str          # forces justification → measurably better calibration
```

The prompt encodes your persona explicitly — this is where "senior engineer, not
news reader" gets operationalized:

> You filter for a senior software engineer working on AI infrastructure and agent
> systems. They want: implementation detail, architecture decisions, benchmark
> methodology, failure modes, production war stories, novel techniques.
> They do NOT want: funding, product launches, consumer apps, policy, or anything
> whose main content is that a company announced something.
>
> Ask of each item: **"Would this change how they build something next week?"**
> If no, `keep: false`.

`keep: false` items still get archived — they're needed for novelty scoring and
weekly trend detection. Filtering means "not in today's report," not "deleted."

### Stage C — Opus deep read (~$0.10/day, ~8 → 5)

Full text is fetched **only now** — for ~8 URLs, not 50. This ordering is the
single biggest cost saver in the design.

```python
class DeepRead(BaseModel):
    priority: Literal["P0", "P1", "P2"]
    summary: str              # one sentence, no hedging
    why_it_matters: str       # 2-3 sentences, engineering consequence
    key_takeaways: list[str]  # 3-5 bullets, concrete and technical
    should_read: Literal["yes-fully", "skim", "skip-summary-is-enough"]
    reading_minutes: int
    prerequisites: list[str]  # what you need to know first — surprisingly useful
```

`should_read` is the most valuable field in the whole system: an honest
"the summary is enough, skip it" saves you more time than any summary does.

**Effort settings** — use `output_config={"effort": "low"}` for triage (mechanical
classification) and `"high"` for synthesis (genuine judgment). Effort is the primary
cost lever on Opus 5.

---

## 11. Trend detection

Trend detection needs history, which is exactly what the archive provides.

**Daily "Today's Trend"** — not a summary of the news, per your brief. The prompt
gets today's clusters *plus* the topic distribution from the trailing 14 days, and
answers: **what shifted?** New topic appearing? Existing topic accelerating?
Something that was hot going quiet? A consensus forming or breaking?

**Weekly detection** runs on real aggregates:

```sql
-- Topic momentum: this week vs. the 3 weeks before it
WITH weekly AS (
  SELECT topic, strftime('%Y-%W', published_at) AS wk, COUNT(*) AS n
  FROM items JOIN item_topics USING(item_id)
  WHERE published_at > date('now', '-28 days')
  GROUP BY topic, wk
)
SELECT topic,
       MAX(CASE WHEN wk = strftime('%Y-%W','now') THEN n END) AS this_week,
       AVG(CASE WHEN wk < strftime('%Y-%W','now') THEN n END) AS baseline
FROM weekly GROUP BY topic
HAVING this_week > baseline * 1.8 AND this_week >= 3;
```

Signals worth computing:
- **Emerging** — topic with ≥3 items this week, ~0 in the prior 3 weeks
- **Accelerating** — ≥1.8× the 3-week baseline
- **Cross-lab convergence** — the strongest signal there is. When OpenAI, Anthropic,
  *and* DeepMind all publish on the same topic within a week, that's a real shift.
- **Research→production lag** — an arXiv topic from ~6 weeks ago now showing up in
  engineering blogs and GitHub repos. This is the "what deserves deeper learning"
  answer.
- **Overhyped detector** — high mention volume, low technical depth (many items,
  all short, none with benchmarks or code). Directly serves your "what appears
  overhyped" weekly section.

---

## 12. Memory

Four distinct kinds, deliberately separated:

1. **Item memory** — every item ever seen, forever. Powers dedupe + novelty.
2. **Topic memory** — rolling topic→count→week table. Powers trends.
3. **Report memory** — every report emitted. Lets the daily prompt say "you covered
   this Tuesday; here's only what's *new*" instead of re-explaining context.
4. **Feedback memory** — the piece that makes it improve over time.

**The feedback loop.** Add "👍 more like this / 👎 less" buttons to the Lark card
(Card JSON v2 supports `button` elements with `behaviors`). Route to a tiny handler
(Cloudflare Worker free tier or a `repository_dispatch` webhook). Each vote appends
`(item_embedding, +1/-1)` to a feedback table.

Then: **shift the interest vector toward liked items and away from disliked ones.**

```python
interest_vec = normalize(
    base_interest_vec
    + 0.3 * mean(liked_embeddings[-100:])
    - 0.2 * mean(disliked_embeddings[-100:])
)
```

Keep the base vector as an anchor — with pure feedback drift the system slowly
converges on whatever you clicked most, and stops surfacing new territory. Anchoring
prevents that failure mode.

This is genuinely optional for v1 — but it's what makes month-6 output better than
month-1 output, which no amount of prompt engineering achieves.

---

## 13. Caching

| Layer | Mechanism | Saves |
|---|---|---|
| HTTP conditional | ETag/Last-Modified per source → 304 | ~70% of fetch time |
| Full text | `content_hash` → extracted text in SQLite | Re-extraction on retries |
| Embeddings | Store as BLOB, keyed by content hash | ~200ms/item CPU |
| LLM prompt cache | `cache_control: {"type": "ephemeral"}` on the system block | ~90% on cached prefix |
| Failed URLs | 24h negative cache | Hammering dead links |

**Prompt caching detail that matters:** the cache is a *prefix match*, so the
persona/instruction block must be byte-identical across calls and must come **first**.
Never interpolate today's date into the system prompt — that invalidates the entire
cache every single day. Put volatile content (the item list) after the cache
breakpoint. Minimum cacheable prefix on Opus 5 is 512 tokens; your persona prompt
will comfortably exceed that.

---

## 14. Lark bot implementation

**Verified constraints from the Lark docs:**
- Request body ≤ **20 KB** (hard limit — this drove the "card + link" decision)
- Rate limit: 100 calls/min, 5 calls/sec per bot
- Security: custom keywords, IP allowlist, or **HMAC-SHA256 signature** (use signing)
- **Card JSON v2** (`"schema": "2.0"`) supports `collapsible_panel`
- Max 5 levels of container nesting

**Signature** — timestamp + secret, note the unusual construction (the string is the
*key*, message is empty):

```python
def sign(timestamp: str, secret: str) -> str:
    key = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
```

**Card structure** (verified field names from the v2 collapsible-panel docs):

```python
{
  "msg_type": "interactive",
  "card": {
    "schema": "2.0",
    "header": {"title": {"tag": "plain_text", "content": "🧠 AI Engineering Daily"},
               "subtitle": {"tag": "plain_text", "content": "2026-08-11 · 52 found · 5 worth reading"},
               "template": "blue"},
    "body": {"elements": [
      {"tag": "markdown", "content": "## 🔴 P0\n**[Title](url)** · `Anthropic Engineering`\n\nOne-sentence summary.\n\n**Why it matters:** ..."},
      {"tag": "collapsible_panel",
       "expanded": False,                          # collapsed by default
       "header": {"title": {"tag": "markdown", "content": "**Key takeaways**"},
                  "icon_position": "right", "icon_expanded_angle": 180},
       "elements": [{"tag": "markdown", "content": "- point\n- point"}]},
      {"tag": "hr"},
      {"tag": "markdown", "content": "## 📈 Today's Trend\n..."},
      {"tag": "markdown", "content": "## ⏱️ If you only have 30 minutes\n1. ...\n2. ..."},
      {"tag": "action", "actions": [
        {"tag": "button", "text": {"tag": "plain_text", "content": "Full report →"},
         "type": "primary", "behaviors": [{"type": "open_url", "default_url": ARCHIVE_URL}]}]}
    ]}
  }
}
```

**Emoji only in section headers**, per your spec — `## 🔴 P0`, `## 📈 Today's Trend`,
`## 🛠️ GitHub`, `## ⏱️ Reading Recommendation`. Never inside body prose.

**The 20KB guard is mandatory, not optional:**

```python
def send(card: dict) -> None:
    payload = json.dumps(card, ensure_ascii=False)
    while len(payload.encode()) > 19_000:      # 1KB headroom
        card = drop_lowest_priority_section(card)
        payload = json.dumps(card, ensure_ascii=False)
```

Degrade gracefully: drop P2 first, then collapse takeaway panels, then truncate
summaries. Never let a busy day produce a failed push. And **always** write the full
markdown archive before sending the card — the archive is the source of truth, the
card is a notification.

---

## 15. Cost optimization

Projected monthly cost at ~50 items/day:

| Item | Monthly |
|---|---|
| GitHub Actions (public repo) | **$0** |
| Embeddings (local `bge-small`) | **$0** |
| Haiku triage — 30 × ~15k in / 2k out | ~**$0.60** |
| Opus deep read — 30 × ~25k in / 4k out | ~**$6.50** |
| Opus weekly synthesis — 4 × ~60k in / 8k out | ~**$1.70** |
| **Total** | **≈ $9/month** |

Levers, highest to lowest impact:

1. **Filter before you pay.** Full text is fetched only for post-triage survivors.
   Sending 50 full articles to Opus instead of 8 would be a ~6× cost increase for
   no quality gain.
2. **Prompt caching.** Persona blocks are stable and reused across every call —
   ~90% discount on the cached prefix. Requires byte-identical prefixes (§13).
3. **Effort tuning.** `effort: "low"` on triage, `"high"` on synthesis. This is the
   main Opus 5 cost dial.
4. **Batch API** for the weekly job — 50% off, and a weekly report has no latency
   requirement whatsoever. Submit, poll, render.
5. **Right-size the model.** Haiku 4.5 ($1/$5 per MTok) for classification; Opus 5
   ($5/$25) only where judgment is genuinely required.
6. **Hard budget guard.** Track token spend per run in SQLite; abort and send a
   plain-text alert card if a run exceeds 3× the trailing median. Protects against
   a runaway loop or a source that suddenly returns 10,000 items.

---

## 16. Build order

Ship a working end-to-end pipeline before adding sophistication. Each phase produces
something you actually use.

| Phase | Scope | Outcome |
|---|---|---|
| **1** | 6 RSS sources → SQLite → keyword filter → plain-text Lark push | Something lands on your phone tomorrow morning |
| **2** | + arXiv, GitHub, HN · embeddings · dedupe · interest scoring | Real filtering; noise drops sharply |
| **3** | + Haiku triage · Opus deep read · clustering · Card v2 + archive | The actual product |
| **4** | + weekly synthesis · trend SQL · momentum detection | Compounding value from the archive |
| **5** | + feedback buttons · interest-vector drift · dead-source alerts | Improves while you use it |

**Phase 1 is deliberately crude** — no LLM, no embeddings. Its purpose is to prove
the fetch → store → push loop and the Lark webhook end to end. Everything after that
is improving filter quality against a pipeline that already works.

---

## 17. Failure modes to design against

Named up front, because each one is a real way this class of system dies:

| Failure | Mitigation |
|---|---|
| A feed 404s permanently and quietly | Track consecutive failures; surface "3 dead sources" in the card footer |
| A source floods (1000 items) | Per-source cap of 50/run before scoring |
| LLM returns malformed JSON | Structured outputs (`output_config.format`); on failure fall back to rule-only ranking and still ship |
| Card exceeds 20KB | Progressive degradation (§14) |
| Everything gets filtered out | Floor: always report top 3 by rule score, labeled "quiet day" |
| Prompt cache silently missing | Assert `cache_read_input_tokens > 0` after warmup; log if zero |
| Anthropic API down | Ship the rule-scored report with a "LLM unavailable" banner. Never skip a day silently |
| Actions run skipped | Next run backfills — always query "since last successful run", never "since 24h ago" |

That last one is worth emphasizing: **make the fetch window derive from
`last_successful_run` in the DB, not from wall-clock**. It makes missed runs
self-healing instead of creating permanent gaps.
