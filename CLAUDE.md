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
- `video.py` — `ingest(url)` does `resolve_url()` → `crawl_metadata()` → mkdir → `asyncio.gather(crawl_comments(), download_video())` → persist. `return_exceptions=True` plus an explicit check, so one failing step is logged without cancelling the other.

Build a crawler with `await Cls.create(pool)`; both bases are async context managers that close their context.

`core/selectors.py` — **all** DOM selectors live in `providers/<platform>/selectors.yaml`, never in Python. Each entry may list `fallbacks`; when one fires it logs a WARNING naming the platform and key, which is the earliest signal a provider changed its markup. `SelectorSet` serves both Playwright (`locate`/`all`/`wait_for`) and BeautifulSoup (`select`/`select_one`) from the same entry.

`validate_config()` runs on the main thread before any browser starts: it validates the YAML and each crawler's `required_selectors`, so a bad or incomplete file exits cleanly instead of dying mid-crawl. **Adding a `self.selectors.<...>("new_key")` call means adding that key to `required_selectors`.**

`core/config.py` — `ConfigError`, `data_dir()`, `required_path()`. Everything env-driven fails with the variable name in the message.

`schema.py` — one `VideoSchema` for all providers; `CommentSchema` → `SingleCommentSchema` for nested replies. Fields a platform does not publish are `None`, not `0` or `""` — TikTok has no `title`, `channel_id` or `subscribers`, and either platform can hide a count.

`core/log.py` — use `get_logger(__name__)`. Named `log.py` to avoid shadowing the stdlib `logging` it imports.

## Waiting

Never `time.sleep`. Use `selectors.wait_for(...)`, which returns the moment the element appears. Where a page must settle (infinite scroll), poll for the element count to grow with a bounded `asyncio.sleep(0.25)` and comment why.

## Platform differences

- **TikTok** — metadata parsed from the `__UNIVERSAL_DATA_FOR_REHYDRATION__` JSON blob over `httpx` (not the browser); video streamed from `playAddr` with spoofed headers/cookies. Comments scraped from the live DOM, then parsed by `__parse_comments`, a pure BeautifulSoup function taking HTML — the seam unit tests use.
- **YouTube** — metadata and download go through `yt_dlp`, which is synchronous, so both run under `asyncio.to_thread`. Only comments use the browser.

## Auth

TikTok needs an exported logged-in cookie file (`{"cookies": [{name, value, domain, ...}]}`), loaded into the browser context via `cookies()`. Never committed — verified with `git log --all -- '*cookies.json'`; `secrets/` and `*_cookies.json` are gitignored. YouTube needs no credentials.
