"""Connection pool and migrations. Raw SQL throughout - no ORM."""

from pathlib import Path

import asyncpg

from vidcorpus.core.config import ConfigError, database_url
from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)
MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


async def connect(min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    try:
        return await asyncpg.create_pool(
            database_url(), min_size = min_size, max_size = max_size
        )
    except OSError as e:
        raise ConfigError(
            f"cannot reach Postgres at {database_url()}: {e}. "
            "Is `docker compose up -d postgres` running?"
        ) from e


async def current_version(pool: asyncpg.Pool) -> int:
    """0 when the database is empty."""
    exists = await pool.fetchval(
        "SELECT to_regclass('public.schema_version') IS NOT NULL"
    )
    if not exists:
        return 0
    return await pool.fetchval("SELECT coalesce(max(version), 0) FROM schema_version")


async def migrate(pool: asyncpg.Pool) -> int:
    """Apply every migration newer than the recorded version. Returns the new one."""
    applied = await current_version(pool)
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise ConfigError(f"no migrations found in {MIGRATIONS_DIR}")

    for path in files:
        version = int(path.name.split("_", 1)[0])
        if version <= applied:
            continue
        LOGGER.info(f"Applying migration {path.name}")
        # Each file wraps itself in BEGIN/COMMIT.
        await pool.execute(path.read_text(encoding = "utf-8"))
        applied = version

    return applied
