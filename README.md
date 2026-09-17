# VidCorpus

Video ingestion and ASR platform. Crawls TikTok and YouTube through a real
browser, stores metadata and comment threads in Postgres with full provenance,
and — from P4 — transcribes the audio on GPU workers that scale to zero. Every
row records the parser and selector version that produced it.

**Status:** P1 complete (crawler rewrite). P2 adds scheduled ingest and S3;
P3 a Go coordinator and Kafka; P4 Whisper ASR on scale-to-zero GPU; P5 the
operational surface on EKS.

---

## Why this exists

Most scrapers stop at "it downloads videos". The interesting problems start
after that:

- **Providers change their DOM without warning.** A selector that worked in
  March silently returns nothing in April. Selectors here live in versioned
  YAML with fallback chains, and a fallback firing logs a warning — the earliest
  signal that markup has moved.
- **Browsers are expensive.** One Chrome per worker cost ~531 MB. One Chromium
  process with many contexts costs ~97 MB per worker, measured — a 5.5×
  reduction at eight workers, and startup drops from 10.2s to 0.5s.
- **Crawled data is not reproducible.** A video can be deleted; a view count
  moves on. So counts are appended rather than overwritten, and every row
  records the parser and selector version that produced it.
- **Runs get killed.** Job state lives in Postgres, so a restart resumes
  instead of recrawling.

---

## Architecture

```
                        ┌──────────────┐
      keywords ────────▶│  links       │  async generator, streams URLs
      channels          │  crawler     │
                        └──────┬───────┘
                               │ asyncio.Queue
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
              ┌─────────┐┌─────────┐┌─────────┐
              │ video   ││ video   ││ video   │   N tasks, one browser
              │ crawler ││ crawler ││ crawler │   context each
              └────┬────┘└────┬────┘└────┬────┘
                   │          │          │
        ┌──────────┴──────────┴──────────┴─────────┐
        │                                          │
        ▼                                          ▼
┌───────────────┐                        ┌──────────────────┐
│  Postgres     │                        │  blobs on disk   │
│  metadata     │                        │  (S3 from P2)    │
│  comments     │                        └──────────────────┘
│  provenance   │        ╔═══════════════════════════════╗
│  stats history│        ║  P3+: Kafka event log,        ║
│  crawl_jobs   │        ║  Go coordinator, Whisper ASR  ║
└───────────────┘        ║  on scale-to-zero GPU (EKS)   ║
                         ╚═══════════════════════════════╝
```

---

## Quick start

```bash
git clone https://github.com/tanhoangkhoanguyen/VidCorpus.git
cd VidCorpus
cp .env.example .env          # then point TIKTOK_COOKIES_PATH at your cookie file

docker compose up -d postgres

cd services/crawler-py
python -m venv .venv && .venv/Scripts/activate    # or source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
vidcorpus-db migrate

vidcorpus tiktok  -k "du lịch đà nẵng" -w 4
vidcorpus youtube -c SomeChannel --headed
```

Then query it — the corpus is plain SQL, no ORM in the way:

```sql
SELECT c.handle, count(*) AS videos, sum(v.views) AS total_views
  FROM channels c JOIN videos v ON v.channel_id = c.id
 WHERE v.published_at >= now() - interval '30 days'
 GROUP BY c.handle ORDER BY total_views DESC LIMIT 5;
```

**Tests:**

```bash
pytest -m "not integration"   # 35 tests, 0.4s, no Docker or browser needed
pytest                        # adds 18 more against a real Postgres
```

Offline is fast because fetching and parsing are separate: `parse_*` is pure,
so saved pages drive it.

---

## The use case is deliberately undecided

HuggingFace, fine-tuning, analysis — all plausible, none chosen. So the schema
forecloses nothing: provenance on every row, counts appended rather than
overwritten. Both are cheap now and impossible to add later.

**Non-goals:** a general-purpose scraping framework, real-time anything,
defeating anti-bot systems, a UI, training models, or picking an export format
before there is a reader.

---

## Decisions

Each was chosen against a named alternative, with a trigger for revisiting it.

| Decision | Because | Would change if |
| --- | --- | --- |
| **Playwright over Selenium** | One browser process, one context per worker: 531 MB → 97 MB per worker measured at eight | Contexts stopped isolating cookies |
| **Postgres, raw SQL, no ORM** | The interesting parts are `ON CONFLICT`, partial indexes and window functions — exactly what an ORM hides | More than one writer |
| **Selectors in YAML, not Python** | Markup churn is expected; a fallback firing is the earliest warning | A provider shipped a stable API |
| **Store only published fields** | Keeping the raw payload doubled row size for fields nothing read | A field turns out to be needed while the videos still exist |
| **Append-only stats** | A view count that moved cannot be recovered | Never |
| **Kafka in P3, not P1** | An event log replaying into nothing proves nothing | A second consumer reads the same stream |
| **EKS in P5, not now** | Only justified by GPU scale-to-zero: ~$380/month always-on against ~$50 at 3h/day | P4 is cut — then Compose on one instance is correct |
| **No dbt, DVC, MLflow, Airflow or RAG** | Each solves a problem this project does not have at the scale it has it | Multiple transformation authors; real DAG structure; anything trained |

---

## Layout

```
services/crawler-py/          Python crawler
  src/vidcorpus/
    core/                     browser pool, selectors, config, base crawler
    providers/<platform>/      links.py + video.py + selectors.yaml
    storage/                  repository, migrations runner, db CLI
  migrations/                 plain SQL, recorded in schema_version
  tests/                      unit (offline fixtures) + integration (testcontainers)
```

[CLAUDE.md](CLAUDE.md) is the orientation for working in this repo.
