from vidcorpus.providers.tiktok.links import COOKIES_ENV_VAR, TikTokLinksCrawler
from vidcorpus.providers.tiktok.video import TikTokVideoCrawler
from vidcorpus.providers.youtube.links import YouTubeLinksCrawler
from vidcorpus.providers.youtube.video import YoutubeVideoCrawler
from vidcorpus.core.config import ConfigError, required_path
from vidcorpus.core.log import get_logger

import sys, threading, os, queue, warnings
warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

LOGGER = get_logger(__name__)

class CrawlerService:
    def __init__(
            self, 
            keywords: list[str],
            channels: list[str],
            producer_workers: int,
            consumer_workers: int
        ):
        self.keywords = keywords
        self.channels = channels
        self.producer_workers = producer_workers
        self.consumer_workers = consumer_workers

        self.link_queue = queue.Queue()
        self.existing_links = set()

    def __producer(self):
        crawler = TikTokLinksCrawler()
        for keyword in self.keywords:
            for link in crawler.run(keyword = keyword):
                if link in self.existing_links:
                    continue
                self.existing_links.add(link)
                self.link_queue.put(link)

        for _ in range(self.consumer_workers):
            self.link_queue.put(None)
        crawler.quit_driver()

    def __consumer(self):
        crawler = TikTokVideoCrawler()
        while True:
            link = self.link_queue.get()
            if link is None:
                break
            try:
                crawler.run(link)
            except Exception as e:
                LOGGER.error(f"Failed processing {link}\n\t{e}")
        crawler.quit_driver()

    def run(self):
        producer_thread = threading.Thread(target = self.__producer)
        producer_thread.start()

        with ThreadPoolExecutor(max_workers = self.consumer_workers) as executor:
            for _ in range(self.consumer_workers):
                executor.submit(self.__consumer)
        producer_thread.join()

def validate_environment(workers: int):
    cpu_count = os.cpu_count()
    LOGGER.info(f"Available CPUs: {cpu_count}")

    if cpu_count < workers:
        LOGGER.error("Please modify the workers")
        raise SystemExit()

def run(
        producer_workers: int = 1,
        consumer_workers: int = 4
    ):
    validate_environment(producer_workers + producer_workers)

    # Tiktok
    keywords = ["du lịch đà nẵng"]
    # channels = ["whoisnikorain"]

    # Youtube
    # keywords = ["lovestruck in the city"]
    # channels = ["AnimeProAnimeonPiano"]

    crawler = CrawlerService(
        keywords = keywords,
        channels = [],
        producer_workers = producer_workers,
        consumer_workers = consumer_workers
    )
    crawler.run()

def main():
    load_dotenv()
    try:
        # Fail here rather than inside the producer thread, where the exception
        # would be swallowed and the consumers would block on an empty queue.
        required_path(COOKIES_ENV_VAR)
        run()
    except ConfigError as e:
        LOGGER.error(str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()