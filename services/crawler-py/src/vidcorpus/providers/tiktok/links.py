from vidcorpus.core.browser import ChromeDriver
from vidcorpus.core.log import get_logger

import json, time, re
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

LOGGER = get_logger(__name__)

class TikTokLinksCrawler:
    def __init__(self):
        self.driver = ChromeDriver().get_driver()
        self.driver.get("https://www.tiktok.com")
        self.__load_cookies()

    def __load_cookies(self):
        try:
            with open("VideoCrawler/providers/tiktok/tiktok_cookies.json", "r", encoding = "utf-8") as f:
                cookie_data = json.load(f)
            for c in cookie_data["cookies"]:
                self.driver.add_cookie({
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", None),
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                })
        except:
            LOGGER.error("No tiktok_cookies.json found")
            raise

    def scroll_page(
            self, 
            scrolls: int = 1
        ):
        body = self.driver.find_element(By.TAG_NAME, "body")
        for _ in range(scrolls):
            body.send_keys(Keys.END)
            time.sleep(2)

    def extract_video_links(self):
        pattern = re.compile(
            r"https://www\.tiktok\.com/@[A-Za-z0-9_.-]+/video/[0-9]+"
        )
        links = self.driver.find_elements(By.XPATH, "//a[@href]")
        for link in links:
            href = link.get_attribute("href")
            if href and pattern.search(href):
                yield href

    def crawl_by_keyword(
            self, 
            keyword: str, 
            scrolling: int = 1
        ):
        encoded = keyword.replace(" ", "%20")
        url = f"https://www.tiktok.com/search?q={encoded}"
        self.driver.get(url)
        time.sleep(2)

        for _ in range(scrolling):
            self.driver.execute_script("""
                const items = document.querySelectorAll('a[href*="/video/"]');
                if (items.length) items[items.length - 1].scrollIntoView();
            """)
            time.sleep(2)

        yield from self.extract_video_links()

    def crawl_by_channel(
            self, 
            channel: str, 
            scrolling: int = 1
        ):
        url = f"https://www.tiktok.com/@{channel}"
        self.driver.get(url)
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