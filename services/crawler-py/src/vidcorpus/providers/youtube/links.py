from collections.abc import AsyncIterator

from vidcorpus.core.crawler import BaseLinksCrawler, scroll_until_more
from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)


class YouTubeLinksCrawler(BaseLinksCrawler):
    name = "youtube"
    required_selectors = {"video_link"}

    def search_url(self, keyword: str) -> str:
        return f"https://www.youtube.com/results?search_query={keyword.replace(' ', '+')}"

    def channel_url(self, channel: str) -> str:
        return f"https://www.youtube.com/@{channel}/videos"

    async def load_more_results(self, rounds: int) -> None:
        await scroll_until_more(
            self.page, self.selectors, "video_link", rounds,
            lambda: self.page.keyboard.press("End"),
        )

    async def extract_video_links(self) -> AsyncIterator[str]:
        for video in await self.selectors.all(self.page, "video_link"):
            href = await video.get_attribute("href")
            if href and "watch" in href:
                yield href
