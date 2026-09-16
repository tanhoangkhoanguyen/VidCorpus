"""Shared crawler shape. Providers supply the platform-specific parts only."""

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import requests

from vidcorpus.core.config import data_dir
from vidcorpus.core.log import get_logger
from vidcorpus.core.selectors import load as load_selectors
from vidcorpus.schema import VideoSchema

LOGGER = get_logger(__name__)


class BaseLinksCrawler(ABC):
    """Streams video URLs from a platform's search and channel pages."""

    name: str
    #: Selector keys this crawler reads, checked against the YAML at startup.
    required_selectors: set[str] = set()

    def __init__(self, driver):
        self.driver = driver
        self.selectors = load_selectors(self.name)

    @classmethod
    def validate_config(cls) -> None:
        """Raise ConfigError if this provider is not ready to run.

        Called on the main thread before any crawler is built, so a missing
        setting surfaces as a clean exit instead of dying inside a worker.
        Subclasses that need credentials extend this.
        """
        load_selectors(cls.name).require(cls.required_selectors)

    @abstractmethod
    def search_url(self, keyword: str) -> str:
        """URL of the search results page for keyword."""

    @abstractmethod
    def channel_url(self, channel: str) -> str:
        """URL of the channel's video listing."""

    @abstractmethod
    def extract_video_links(self) -> Iterator[str]:
        """Yield video URLs from the page currently loaded."""

    @abstractmethod
    def load_more_results(self, rounds: int) -> None:
        """Trigger the page to load another batch. Platforms paginate differently."""

    def __links_from_page(self, url: str, scroll_rounds: int) -> Iterator[str]:
        self.driver.get(url)
        self.load_more_results(scroll_rounds)
        yield from self.extract_video_links()

    def links_for_keyword(self, keyword: str, scroll_rounds: int = 1) -> Iterator[str]:
        yield from self.__links_from_page(self.search_url(keyword), scroll_rounds)

    def links_for_channel(self, channel: str, scroll_rounds: int = 1) -> Iterator[str]:
        yield from self.__links_from_page(self.channel_url(channel), scroll_rounds)

    def discover(self, keyword: str = None, channel: str = None) -> Iterator[str]:
        """Yield every video URL reachable from the given keyword and/or channel."""
        if keyword:
            yield from self.links_for_keyword(keyword)
        if channel:
            yield from self.links_for_channel(channel)

    def close(self) -> None:
        self.driver.quit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class BaseVideoCrawler(ABC):
    """Fetches one video's blob, metadata and comments, then persists them."""

    name: str
    url_prefixes: tuple[str, ...]
    #: Selector keys this crawler reads, checked against the YAML at startup.
    required_selectors: set[str] = set()

    def __init__(self, driver):
        self.driver = driver
        self.selectors = load_selectors(self.name)
        self.url = None
        self.storage_path = None
        self.metadata = VideoSchema()

    @classmethod
    def validate_config(cls) -> None:
        """Raise ConfigError if this provider is not ready to run."""
        load_selectors(cls.name).require(cls.required_selectors)

    @abstractmethod
    def crawl_metadata(self) -> None:
        """Populate self.metadata for self.url."""

    @abstractmethod
    def crawl_comments(self) -> None:
        """Append comment threads to self.metadata.comments."""

    @abstractmethod
    def download_video(self) -> None:
        """Write the video blob into self.storage_path."""

    def resolve_url(self) -> bool:
        """Follow redirects and confirm the result belongs to this platform.

        Rewrites self.url to the resolved address; returns False if it turned
        out to belong to another platform.
        """
        final_url = requests.get(self.url, allow_redirects = True).url
        if not final_url.startswith(self.url_prefixes):
            LOGGER.warning(f"{self.url} resolves to {final_url}, not a {self.name} video")
            return False
        self.url = final_url
        return True

    def write_metadata_file(self) -> None:
        file_path = self.storage_path / f"{self.name}_{self.metadata.id}.json"
        try:
            with open(file_path, "w", encoding = "utf-8") as f:
                json.dump(self.metadata.model_dump(), f, ensure_ascii = False, indent = 4)
            LOGGER.info(f"Saved metadata for {self.name} video {self.metadata.id}")
        except OSError as e:
            LOGGER.error(f"Unable to save metadata for video {self.metadata.id}: {e}")

    def ingest(self, url: str) -> None:
        """Fetch one video end to end: metadata, comments and blob, then save."""
        self.url = url
        if not self.resolve_url():
            return

        self.crawl_metadata()
        self.storage_path = data_dir() / self.metadata.id
        self.storage_path.mkdir(parents = True, exist_ok = True)

        with ThreadPoolExecutor(max_workers = 2) as executor:
            futures = [
                executor.submit(self.crawl_comments),
                executor.submit(self.download_video),
            ]
        # submit() swallows exceptions; result() re-raises so a failed step is visible.
        for future in futures:
            try:
                future.result()
            except Exception as e:
                LOGGER.error(f"Step failed for video {self.metadata.id}: {e}")

        self.write_metadata_file()

    def close(self) -> None:
        self.driver.quit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
