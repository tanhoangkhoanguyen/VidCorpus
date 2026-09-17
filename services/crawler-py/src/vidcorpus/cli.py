import argparse
import asyncio
import sys

from dotenv import load_dotenv

from vidcorpus.core.browser import BrowserPool
from vidcorpus.core.config import ConfigError
from vidcorpus.core.log import get_logger
from vidcorpus.providers import registry

LOGGER = get_logger(__name__)

#: Queue marker telling one consumer to stop.
SENTINEL = None
#: Shell convention for "killed by SIGINT" (128 + signal 2).
EXIT_INTERRUPTED = 130


class CrawlerService:
    """One producer streams links onto a queue; N consumers drain it.

    All of it runs on one event loop against one browser process - the workers
    are tasks, not threads, and each holds a browser context rather than a
    browser.
    """

    def __init__(
            self,
            platform: str,
            keywords: list[str],
            channels: list[str],
            workers: int
        ):
        self.platform = platform
        self.keywords = keywords
        self.channels = channels
        self.workers = workers

        self.link_queue = asyncio.Queue()
        self.seen_links = set()

    def _enqueue_unseen(self, link: str) -> str | None:
        """Single-threaded: the event loop gives us the check-then-add atomically."""
        if link in self.seen_links:
            return None
        self.seen_links.add(link)
        return link

    async def __producer(self, pool: BrowserPool) -> None:
        try:
            crawler_class = registry.get_links_crawler(self.platform)
            crawler = await crawler_class.create(pool)
            async with crawler:
                for keyword in self.keywords:
                    async for link in crawler.discover(keyword = keyword):
                        if self._enqueue_unseen(link):
                            await self.link_queue.put(link)
                for channel in self.channels:
                    async for link in crawler.discover(channel = channel):
                        if self._enqueue_unseen(link):
                            await self.link_queue.put(link)
        except Exception as e:
            LOGGER.error(f"Producer stopped: {type(e).__name__}: {e}")
        finally:
            # Always send the sentinels, or the consumers wait forever.
            for _ in range(self.workers):
                await self.link_queue.put(SENTINEL)

    async def __consumer(self, pool: BrowserPool) -> None:
        try:
            crawler_class = registry.get_video_crawler(self.platform)
            crawler = await crawler_class.create(pool)
            async with crawler:
                while True:
                    link = await self.link_queue.get()
                    if link is SENTINEL:
                        return
                    try:
                        await crawler.ingest(link)
                    except Exception as e:
                        LOGGER.error(f"Failed processing {link}: {type(e).__name__}: {e}")
        except Exception as e:
            # A worker that cannot start must not take the whole run down.
            LOGGER.error(f"Consumer stopped: {type(e).__name__}: {e}")

    async def crawl(self, headless: bool = True) -> None:
        async with BrowserPool(headless = headless) as pool:
            await asyncio.gather(
                self.__producer(pool),
                *(self.__consumer(pool) for _ in range(self.workers)),
            )
        LOGGER.info(f"Done: {len(self.seen_links)} unique links seen")


def parse_args(argv = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog = "vidcorpus", description = "Crawl videos into a corpus")
    parser.add_argument("platform", choices = registry.supported_platforms())
    parser.add_argument("-k", "--keyword", action = "append", default = [])
    parser.add_argument("-c", "--channel", action = "append", default = [])
    parser.add_argument("-w", "--workers", type = int, default = 4,
                        help = "concurrent video crawlers (default: 4)")
    parser.add_argument("--headed", action = "store_true",
                        help = "show the browser window")
    args = parser.parse_args(argv)
    if not args.keyword and not args.channel:
        parser.error("give at least one --keyword or --channel")
    return args


def main() -> None:
    load_dotenv()
    args = parse_args()
    try:
        # Validates selectors and credentials before any browser starts.
        registry.get_links_crawler(args.platform).validate_config()
        registry.get_video_crawler(args.platform).validate_config()
        asyncio.run(
            CrawlerService(
                platform = args.platform,
                keywords = args.keyword,
                channels = args.channel,
                workers = args.workers,
            ).crawl(headless = not args.headed)
        )
    except ConfigError as e:
        LOGGER.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted")
        sys.exit(EXIT_INTERRUPTED)


if __name__ == "__main__":
    main()
