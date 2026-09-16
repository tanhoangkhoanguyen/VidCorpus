import time
from collections.abc import Iterator

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from vidcorpus.core.browser import ChromeDriver
from vidcorpus.core.crawler import BaseLinksCrawler
from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)
#: YouTube loads results lazily; give each END press this long to fetch more.
FEED_SETTLE_SECONDS = 2


class YouTubeLinksCrawler(BaseLinksCrawler):
    name = "youtube"

    def __init__(self):
        super().__init__(ChromeDriver().get_driver())

    def search_url(self, keyword: str) -> str:
        return f"https://www.youtube.com/results?search_query={keyword.replace(' ', '+')}"

    def channel_url(self, channel: str) -> str:
        return f"https://www.youtube.com/@{channel}/videos"

    def load_more_results(self, rounds: int) -> None:
        time.sleep(FEED_SETTLE_SECONDS)
        body = self.driver.find_element(By.TAG_NAME, "body")
        for _ in range(rounds):
            body.send_keys(Keys.END)
            time.sleep(FEED_SETTLE_SECONDS)

    def extract_video_links(self) -> Iterator[str]:
        for video in self.driver.find_elements(By.XPATH, '//a[@id="video-title"]'):
            href = video.get_attribute("href")
            if href and "watch" in href:
                yield href
