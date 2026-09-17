# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

VidCorpus ingests videos, metadata and comment threads from TikTok and YouTube into a structured corpus. Extraction drives a real browser (no official APIs), so provider code is coupled to platform DOM — selector churn is expected, not exceptional.

Polyglot monorepo: `services/crawler-py/` is the Python crawler; more services come in later phases. Read the relevant `docs/adr/` entry before changing a technology choice.

## Commands

```powershell
cd services\crawler-py
python -m venv .venv; .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
docker compose up -d postgres        # from the repo root
vidcorpus-db migrate
vidcorpus tiktok -k "du lịch đà nẵng" -w 4
vidcorpus youtube -c SomeChannel --headed
ruff check src
```

Python pinned `>=3.10,<3.13`. Subpackages are PEP 420 namespace packages — no `__init__.py` except the top-level version marker. Copy `.env.example` to `.env`; TikTok needs `TIKTOK_COOKIES_PATH`.

## Architecture

Everything is `async`. One Chromium process (`core/browser.py`'s `BrowserPool`) backs every worker; each worker gets its own **context**, not its own browser — that is where the memory saving lives (~79 MB/worker vs ~534 MB under Selenium).

`cli.py` — `CrawlerService` is one producer task feeding an `asyncio.Queue`, N consumer tasks draining it, shut down by one `None` sentinel per consumer. Dedupe needs no lock: check-then-add is atomic on a single event loop. The producer emits sentinels from `finally`, so a crash cannot hang the consumers.

Each provider is a **pair** under `providers/<platform>/`, resolved through `providers/registry.py` (keyed off each class's `name`) — nothing else imports providers directly:

- `links.py` — `async for link in crawler.discover(keyword=...)`. An async generator: links stream rather than materialize.
- `video.py` — `ingest(url)` does `resolve_url()` → **claim the job** → `crawl_metadata()` → mkdir → `asyncio.gather(crawl_comments(), download_video())` → persist to Postgres. `return_exceptions=True` plus an explicit check, so one failing step is logged without cancelling the other. `_reset()` runs first: **one crawler serves many videos, so per-video state must be cleared** or comments accumulate across them.

Build a crawler with `await Cls.create(pool)`; both bases are async context managers that close their context.

`core/selectors.py` — **all** DOM selectors live in `providers/<platform>/selectors.yaml`, never in Python. Each entry may list `fallbacks`; when one fires it logs a WARNING naming the platform and key, which is the earliest signal a provider changed its markup. `SelectorSet` serves both Playwright (`locate`/`all`/`wait_for`) and BeautifulSoup (`select`/`select_one`) from the same entry.

`validate_config()` runs on the main thread before any browser starts: it validates the YAML and each crawler's `required_selectors`, so a bad or incomplete file exits cleanly instead of dying mid-crawl. **Adding a `self.selectors.<...>("new_key")` call means adding that key to `required_selectors`.**

## Storage

Postgres is the source of truth for metadata and text; blobs stay on disk under `data/<video_id>/`. Raw SQL throughout — no ORM.

`storage/repository.py` — one transaction per video. Only the fields a provider actually publishes are stored; no raw provider payload. Two rules shape the rest:

1. **Record provenance.** `parser_version`, `selectors_version`, `crawled_at` — so a later parser fix can re-derive only the rows that need it.
2. **Append-only for anything that moves.** Recrawling upserts `videos` but *appends* to `video_stats_history`. Overwriting `views` destroys the only copy of what it was.

`crawl_jobs` makes restarts cheap: `claim_job()` returns False for a URL already done, so a killed run resumes instead of recrawling. `CrawlerService` seeds `seen_links` from it at startup.

`vidcorpus-db migrate|version`.

Migrations are plain SQL in `migrations/NNN_*.sql`, each wrapping itself in `BEGIN/COMMIT` and recorded in `schema_version`. `docs/queries.md` has worked examples with real output and `EXPLAIN` plans.

`core/config.py` — `ConfigError`, `data_dir()`, `database_url()`, `required_path()`. Everything env-driven fails with the variable name in the message.

`schema.py` — one `VideoSchema` for all providers; `CommentSchema` → `SingleCommentSchema` for nested replies. Fields a platform does not publish are `None`, not `0` or `""` — TikTok has no `title`, `channel_id` or `subscribers`, and either platform can hide a count.

`core/log.py` — use `get_logger(__name__)`. Named `log.py` to avoid shadowing the stdlib `logging` it imports.

## Waiting

Never `time.sleep`. Use `selectors.wait_for(...)`, which returns the moment the element appears. Where a page must settle (infinite scroll), poll for the element count to grow with a bounded `asyncio.sleep(0.25)` and comment why.

## Platform differences

- **TikTok** — metadata parsed from the `__UNIVERSAL_DATA_FOR_REHYDRATION__` JSON blob over `httpx` (not the browser); video streamed from `playAddr` with spoofed headers/cookies. Comments scraped from the live DOM, then parsed by `__parse_comments`, a pure BeautifulSoup function taking HTML — the seam unit tests use.
- **YouTube** — metadata and download go through `yt_dlp`, which is synchronous, so both run under `asyncio.to_thread`. Only comments use the browser.

## Auth

TikTok needs an exported logged-in cookie file (`{"cookies": [{name, value, domain, ...}]}`), loaded into the browser context via `cookies()`. Never committed — verified with `git log --all -- '*cookies.json'`; `secrets/` and `*_cookies.json` are gitignored. YouTube needs no credentials.
