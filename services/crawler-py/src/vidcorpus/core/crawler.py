"""Shared crawler shape. Providers supply the platform-specific parts only."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import httpx
from playwright.async_api import BrowserContext, Page

from vidcorpus.core.browser import BrowserPool
from vidcorpus.core.config import data_dir
from vidcorpus.core.log import get_logger
from vidcorpus.core.selectors import load as load_selectors
from vidcorpus.schema import VideoSchema

if TYPE_CHECKING:
    from vidcorpus.storage.repository import Repository

LOGGER = get_logger(__name__)

#: How long to wait for lazily-loaded content after a scroll, and how often to
#: check. Bounded so a page that stops growing does not stall the crawl.
SETTLE_TIMEOUT = 5.0
SETTLE_POLL = 0.25


async def scroll_until_more(page, selectors, key: str, rounds: int, trigger_load) -> None:
    """Call `trigger_load` up to `rounds` times, waiting for more `key` elements.

    Infinite feeds fire no load event, so a growing element count is the only
    signal a scroll worked. Polling for it beats a fixed sleep: it continues as
    soon as content arrives, and gives up on a feed that has run out.
    """
    if await selectors.wait_for(page, key) is None:
        LOGGER.warning(f"{selectors.platform}: nothing matched {key!r} on the page")
        return

    loop = asyncio.get_running_loop()
    for _ in range(rounds):
        before = len(await selectors.all(page, key))
        await trigger_load()
        deadline = loop.time() + SETTLE_TIMEOUT
        while loop.time() < deadline:
            await asyncio.sleep(SETTLE_POLL)
            if len(await selectors.all(page, key)) > before:
                break
        else:
            return  # nothing new arrived; another round would be wasted


class BaseLinksCrawler(ABC):
    """Streams video URLs from a platform's search and channel pages."""

    name: str
    #: Selector keys this crawler reads, checked against the YAML at startup.
    required_selectors: set[str] = set()

    def __init__(self, context: BrowserContext, page: Page):
        self.context = context
        self.page = page
        self.selectors = load_selectors(self.name)

    @classmethod
    async def create(cls, pool: BrowserPool) -> "BaseLinksCrawler":
        """Build the crawler on its own browser context."""
        context = await pool.new_context(cookies = cls.cookies())
        return cls(context, await context.new_page())

    @classmethod
    def load_cookies(cls) -> list[dict] | None:
        """Cookies to seed the context with. Providers that need auth override."""
        return None

    @classmethod
    def validate_config(cls) -> None:
        """Raise ConfigError if this provider is not ready to run.

        Runs on the main thread before any browser starts, so a bad config
        exits cleanly instead of dying inside a worker.
        """
        load_selectors(cls.name).require(cls.required_selectors)

    @abstractmethod
    def search_url(self, keyword: str) -> str:
        """URL of the search results page for keyword."""

    @abstractmethod
    def channel_url(self, channel: str) -> str:
        """URL of the channel's video listing."""

    @abstractmethod
    async def extract_video_links(self) -> AsyncIterator[str]:
        """Yield video URLs from the page currently loaded."""

    @abstractmethod
    async def load_more_results(self, rounds: int) -> None:
        """Load more results. Platforms paginate differently."""


    async def __links_from_page(self, url: str, scroll_rounds: int) -> AsyncIterator[str]:
        await self.page.goto(url, wait_until = "domcontentloaded")
        await self.load_more_results(scroll_rounds)
        async for link in self.extract_video_links():
            yield link

    async def links_for_keyword(self, keyword: str, scroll_rounds: int = 1) -> AsyncIterator[str]:
        async for link in self.__links_from_page(self.search_url(keyword), scroll_rounds):
            yield link

    async def links_for_channel(self, channel: str, scroll_rounds: int = 1) -> AsyncIterator[str]:
        async for link in self.__links_from_page(self.channel_url(channel), scroll_rounds):
            yield link

    async def discover(self, keyword: str = None, channel: str = None) -> AsyncIterator[str]:
        if keyword:
            async for link in self.links_for_keyword(keyword):
                yield link
        if channel:
            async for link in self.links_for_channel(channel):
                yield link

    async def close(self) -> None:
        await self.context.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


class BaseVideoCrawler(ABC):
    """Fetches one video's blob, metadata and comments, then persists them."""

    name: str
    url_prefixes: tuple[str, ...]
    #: Selector keys this crawler reads, checked against the YAML at startup.
    required_selectors: set[str] = set()
    #: Narrow viewport: TikTok's comment rail renders wider than it needs to.
    viewport: dict | None = None

    def __init__(self, context: BrowserContext, page: Page, http: httpx.AsyncClient,
                 repository: "Repository"):
        self.context = context
        self.page = page
        self.http = http
        self.repository = repository
        self.selectors = load_selectors(self.name)
        self.url = None
        self.storage_path = None
        self.metadata = VideoSchema()

    @classmethod
    async def create(cls, pool: BrowserPool, repository: "Repository") -> "BaseVideoCrawler":
        context = await pool.new_context(viewport = cls.viewport)
        return cls(
            context,
            await context.new_page(),
            httpx.AsyncClient(follow_redirects = True),
            repository,
        )

    @classmethod
    def validate_config(cls) -> None:
        """Raise ConfigError if this provider is not ready to run."""
        load_selectors(cls.name).require(cls.required_selectors)

    @abstractmethod
    async def crawl_metadata(self) -> None:
        """Populate self.metadata for self.url."""

    @abstractmethod
    async def crawl_comments(self) -> None:
        """Append comment threads to self.metadata.comments."""

    @abstractmethod
    async def download_video(self) -> None:
        """Write the video blob into self.storage_path."""

    async def resolve_url(self) -> bool:
        """Follow redirects and confirm the result belongs to this platform."""
        response = await self.http.get(self.url)
        final_url = str(response.url)
        if not final_url.startswith(self.url_prefixes):
            LOGGER.warning(f"{self.url} resolves to {final_url}, not a {self.name} video")
            return False
        self.url = final_url
        return True

    async def persist(self) -> None:
        await self.repository.save_video(
            platform = self.name,
            video = self.metadata,
            url = self.url,
            selectors_version = self.selectors.version,
        )
        LOGGER.info(
            f"Stored {self.name} video {self.metadata.id} "
            f"({len(self.metadata.comments)} comments)"
        )

    def _reset(self, url: str) -> None:
        """One crawler serves many videos; per-video state must not carry over.

        Without this, comments and hashtags accumulate across every video the
        worker handles, and each save writes the running total.
        """
        self.url = url
        self.storage_path = None
        self.metadata = VideoSchema()

    async def ingest(self, url: str) -> None:
        self._reset(url)
        if not await self.resolve_url():
            return

        # Claim before any work: a second worker, or a rerun after a crash,
        # skips what is already stored.
        if not await self.repository.claim_job(self.name, self.url):
            LOGGER.info(f"{self.url} already crawled, skipping")
            return

        error = None
        try:
            await self.crawl_metadata()
            if not self.metadata.id:
                raise ValueError("metadata carries no video id")

            self.storage_path = data_dir() / self.metadata.id
            self.storage_path.mkdir(parents = True, exist_ok = True)

            # return_exceptions: one failing step must not cancel the other.
            results = await asyncio.gather(
                self.crawl_comments(),
                self.download_video(),
                return_exceptions = True,
            )
            for result in results:
                if isinstance(result, Exception):
                    LOGGER.error(
                        f"Step failed for video {self.metadata.id}: "
                        f"{type(result).__name__}: {result}"
                    )

            await self.persist()
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            raise
        finally:
            await self.repository.finish_job(self.name, self.url, error)

    async def close(self) -> None:
        await self.context.close()
        await self.http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
