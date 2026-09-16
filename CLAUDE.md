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
vidcorpus                      # or: python -m vidcorpus.cli
pytest
ruff check src tests
```

Python pinned `>=3.10,<3.13`. Subpackages are PEP 420 namespace packages — no `__init__.py` except the top-level version marker.

## Architecture

Each provider is a **pair** under `providers/<platform>/`:

- `links.py` — navigates a search or channel page, scrolls, and *yields* video URLs from `run(keyword=..., channel=...)`. Generators are the contract: links stream rather than materialize.
- `video.py` — `run(url)` does validate → `crawl_metadata()` → mkdir → `crawl_comments()` ‖ `download_video()` → persist.

Both create a browser driver eagerly in `__init__` and expose `quit_driver()`; callers must call it.

`core/browser.py` — `ChromeDriver` holds the anti-automation Chrome options. `window_size` is interpolated as `f"--window-size={window_size}"` but callers pass the whole flag, producing a doubled flag.

`schema.py` — one `VideoSchema` for all providers; `CommentSchema` → `SingleCommentSchema` for nested replies. Fields a platform lacks stay at defaults (TikTok never sets `title`/`channel_id`).

`cli.py` — producer/consumer: one thread runs the links crawler onto a `queue.Queue`, N consumers each build their own video crawler and drain it, shut down by one `None` per consumer. **Provider is hardcoded in `__producer`/`__consumer`** — switching platforms means editing those methods.

`core/log.py` — use `get_logger(__name__)`. Named `log.py` to avoid shadowing the stdlib `logging` it imports.

## Platform differences

- **TikTok** — metadata parsed from the `__UNIVERSAL_DATA_FOR_REHYDRATION__` JSON blob via `requests` + BeautifulSoup (not the browser); video pulled from `playAddr` with spoofed headers/cookies. Comments scraped from live DOM via obfuscated class-prefix selectors.
- **YouTube** — metadata and download via `yt_dlp`; only comments use the browser.

## Auth

TikTok needs an exported logged-in cookie file (`{"cookies": [{name, value, domain, ...}]}`). Never committed — verified with `git log --all -- '*cookies.json'`; `secrets/` and `*_cookies.json` are gitignored. YouTube needs no credentials.
