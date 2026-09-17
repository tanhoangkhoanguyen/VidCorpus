import json
import re
from collections.abc import AsyncIterator

from vidcorpus.core.config import ConfigError, required_path
from vidcorpus.core.crawler import BaseLinksCrawler, scroll_until_more
from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)
COOKIES_ENV_VAR = "TIKTOK_COOKIES_PATH"
VIDEO_URL = re.compile(r"https://www\.tiktok\.com/@[A-Za-z0-9_.-]+/video/[0-9]+")

#: TikTok lazy-loads the next page of results only once the last tile is in view.
SCROLL_STEP = """
    const items = document.querySelectorAll('a[href*="/video/"]');
    if (items.length) items[items.length - 1].scrollIntoView();
"""


class TikTokLinksCrawler(BaseLinksCrawler):
    name = "tiktok"
    required_selectors = {"any_link"}

    @classmethod
    def validate_config(cls) -> None:
        super().validate_config()
        required_path(COOKIES_ENV_VAR)

    @classmethod
    def load_cookies(cls) -> list[dict]:
        """Read the exported session cookies, in the shape add_cookies wants."""
        path = required_path(COOKIES_ENV_VAR)
        try:
            raw = json.loads(path.read_text(encoding = "utf-8"))["cookies"]
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path} is not valid JSON: {e}") from e
        except KeyError as e:
            raise ConfigError(f"{path} has no top-level 'cookies' key") from e

        cookies = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain") or ".tiktok.com",
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
            }
            for c in raw
        ]
        LOGGER.info(f"Loaded {len(cookies)} cookies from {path}")
        return cookies

    def search_url(self, keyword: str) -> str:
        return f"https://www.tiktok.com/search?q={keyword.replace(' ', '%20')}"

    def channel_url(self, channel: str) -> str:
        return f"https://www.tiktok.com/@{channel}"

    async def load_more_results(self, rounds: int) -> None:
        await scroll_until_more(
            self.page, self.selectors, "any_link", rounds,
            lambda: self.page.evaluate(SCROLL_STEP),
        )

    async def extract_video_links(self) -> AsyncIterator[str]:
        for link in await self.selectors.all(self.page, "any_link"):
            href = await link.get_attribute("href")
            if href and VIDEO_URL.search(href):
                yield href
