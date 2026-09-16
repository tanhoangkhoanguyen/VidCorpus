import json
import re
import time
from collections.abc import Iterator

from selenium.webdriver.common.by import By

from vidcorpus.core.browser import ChromeDriver
from vidcorpus.core.config import ConfigError, required_path
from vidcorpus.core.crawler import BaseLinksCrawler
from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)
COOKIES_ENV_VAR = "TIKTOK_COOKIES_PATH"
VIDEO_URL = re.compile(r"https://www\.tiktok\.com/@[A-Za-z0-9_.-]+/video/[0-9]+")
#: TikTok loads tiles lazily; give each scroll this long to fetch the next batch.
FEED_SETTLE_SECONDS = 2


class TikTokLinksCrawler(BaseLinksCrawler):
    name = "tiktok"

    @classmethod
    def validate_config(cls) -> None:
        required_path(COOKIES_ENV_VAR)

    def __init__(self):
        # Resolve before launching Chrome so a missing config does not orphan a browser.
        cookies_path = required_path(COOKIES_ENV_VAR)
        super().__init__(ChromeDriver().get_driver())
        self.driver.get("https://www.tiktok.com")
        self.__load_cookies(cookies_path)

    def __load_cookies(self, path):
        try:
            cookies = json.loads(path.read_text(encoding = "utf-8"))["cookies"]
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path} is not valid JSON: {e}") from e
        except KeyError as e:
            raise ConfigError(f"{path} has no top-level 'cookies' key") from e

        for c in cookies:
            self.driver.add_cookie({
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", None),
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
            })
        LOGGER.info(f"Loaded {len(cookies)} cookies from {path}")

    def search_url(self, keyword: str) -> str:
        return f"https://www.tiktok.com/search?q={keyword.replace(' ', '%20')}"

    def channel_url(self, channel: str) -> str:
        return f"https://www.tiktok.com/@{channel}"

    def load_more_results(self, rounds: int) -> None:
        # TikTok's feed only loads more when the last tile enters the viewport,
        # so END-key scrolling does not page it.
        time.sleep(FEED_SETTLE_SECONDS)
        for _ in range(rounds):
            self.driver.execute_script("""
                const items = document.querySelectorAll('a[href*="/video/"]');
                if (items.length) items[items.length - 1].scrollIntoView();
            """)
            time.sleep(FEED_SETTLE_SECONDS)

    def extract_video_links(self) -> Iterator[str]:
        for link in self.driver.find_elements(By.XPATH, "//a[@href]"):
            href = link.get_attribute("href")
            if href and VIDEO_URL.search(href):
                yield href
