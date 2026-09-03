from vidcorpus.core.browser import ChromeDriver
from vidcorpus.core.log import get_logger

import time, warnings
warnings.filterwarnings("ignore")
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

LOGGER = get_logger(__name__)

class YouTubeLinksCrawler:
    def __init__(self):
        self.driver = ChromeDriver().get_driver()

    def scroll_page(
            self, 
            scrolling: int = 1
        ):
        body = self.driver.find_element(By.TAG_NAME, "body")
        for _ in range(scrolling):
            body.send_keys(Keys.END)
            time.sleep(2)

    def extract_video_links(self):
        videos = self.driver.find_elements(By.XPATH, '//a[@id="video-title"]') # '//a[@href and contains(@href,"watch")]'
        for video in videos:
            href = video.get_attribute("href")
            if href and "watch" in href:
                yield href

    def crawl_by_keyword(
            self, 
            keyword: str, 
            scrolling: int = 1
        ):
        encoded = keyword.replace(" ", "+")
        url = f"https://www.youtube.com/results?search_query={encoded}"
        self.driver.get(url)
        time.sleep(2)
        self.scroll_page(scrolling)
        yield from self.extract_video_links()

    def crawl_by_channel(
            self, 
            channel: str, 
            scrolling: int = 1
        ):
        url = f"https://www.youtube.com/@{channel}/videos"
        self.driver.get(url + "/videos")
        time.sleep(2)
        self.scroll_page(scrolling)
        yield from self.extract_video_links()

    def quit_driver(self):
        self.driver.quit()

    def run(
            self,
            keyword: str = None, 
            channel: str = None
        ):
        if keyword:
            yield from self.crawl_by_keyword(keyword)
        if channel:
            yield from self.crawl_by_channel(channel)