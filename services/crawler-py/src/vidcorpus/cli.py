import argparse
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from vidcorpus.core.config import ConfigError
from vidcorpus.core.log import get_logger
from vidcorpus.providers import registry

LOGGER = get_logger(__name__)
SENTINEL = None


class CrawlerService:
    """Producer/consumer: one thread streams links, N threads fetch the videos."""

    def __init__(
            self,
            platform: str,
            keywords: list[str],
            channels: list[str],
            consumer_workers: int
        ):
        self.platform = platform
        self.keywords = keywords
        self.channels = channels
        self.consumer_workers = consumer_workers

        self.link_queue = queue.Queue()
        self.seen_links = set()
        self.seen_lock = threading.Lock()

    def __enqueue(self, link: str) -> None:
        with self.seen_lock:
            if link in self.seen_links:
                return
            self.seen_links.add(link)
        self.link_queue.put(link)

    def __producer(self) -> None:
        try:
            crawler_class = registry.get_links_crawler(self.platform)
            with crawler_class() as crawler:
                for keyword in self.keywords:
                    for link in crawler.discover(keyword = keyword):
                        self.__enqueue(link)
                for channel in self.channels:
                    for link in crawler.discover(channel = channel):
                        self.__enqueue(link)
        except Exception as e:
            LOGGER.error(f"Producer stopped: {e}")
        finally:
            # Always send the sentinels, or the consumers block forever.
            for _ in range(self.consumer_workers):
                self.link_queue.put(SENTINEL)

    def __consumer(self) -> None:
        try:
            crawler_class = registry.get_video_crawler(self.platform)
            with crawler_class() as crawler:
                while True:
                    link = self.link_queue.get()
                    if link is SENTINEL:
                        break
                    try:
                        crawler.ingest(link)
                    except Exception as e:
                        LOGGER.error(f"Failed processing {link}: {e}")
        except Exception as e:
            # A worker that cannot start must not take the whole run down.
            LOGGER.error(f"Consumer stopped: {e}")

    def crawl(self) -> None:
        """Run the producer and consumers to completion."""
        producer_thread = threading.Thread(target = self.__producer)
        producer_thread.start()

        with ThreadPoolExecutor(max_workers = self.consumer_workers) as executor:
            for _ in range(self.consumer_workers):
                executor.submit(self.__consumer)
        producer_thread.join()


def validate_environment(workers: int) -> None:
    cpu_count = os.cpu_count()
    LOGGER.info(f"Available CPUs: {cpu_count}, workers requested: {workers}")
    if cpu_count < workers:
        LOGGER.warning(f"{workers} workers on {cpu_count} CPUs - expect contention")


def parse_args(argv = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog = "vidcorpus", description = "Crawl videos into a corpus")
    parser.add_argument("platform", choices = registry.supported_platforms())
    parser.add_argument("-k", "--keyword", action = "append", default = [])
    parser.add_argument("-c", "--channel", action = "append", default = [])
    parser.add_argument("-w", "--workers", type = int, default = 4, help = "consumer threads")
    args = parser.parse_args(argv)
    if not args.keyword and not args.channel:
        parser.error("give at least one --keyword or --channel")
    return args


def main() -> None:
    load_dotenv()
    args = parse_args()
    # One producer thread plus the consumers.
    validate_environment(1 + args.workers)
    try:
        # Validates selectors and credentials on the main thread, before any
        # browser or worker starts.
        registry.get_links_crawler(args.platform).validate_config()
        registry.get_video_crawler(args.platform).validate_config()
        CrawlerService(
            platform = args.platform,
            keywords = args.keyword,
            channels = args.channel,
            consumer_workers = args.workers,
        ).crawl()
    except ConfigError as e:
        LOGGER.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
