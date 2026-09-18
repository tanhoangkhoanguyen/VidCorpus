"""Database maintenance: migrations."""

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from vidcorpus.core.config import ConfigError
from vidcorpus.core.log import get_logger
from vidcorpus.storage.database import connect, current_version, migrate

LOGGER = get_logger(__name__)


async def _run(command: str) -> None:
    pool = await connect()
    try:
        if command == "migrate":
            LOGGER.info(f"Schema is at version {await migrate(pool)}")
        elif command == "version":
            LOGGER.info(f"Schema version: {await current_version(pool)}")
    finally:
        await pool.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog = "vidcorpus-db")
    parser.add_argument("command", choices = ["migrate", "version"])
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.command))
    except ConfigError as e:
        LOGGER.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
