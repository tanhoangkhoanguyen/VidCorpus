import time

import yt_dlp
from pydantic import ValidationError
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from vidcorpus.core.browser import ChromeDriver
from vidcorpus.core.crawler import BaseVideoCrawler
from vidcorpus.core.log import get_logger
from vidcorpus.schema import CommentSchema, SingleCommentSchema, VideoSchema

LOGGER = get_logger(__name__)

class YouTubeVideoCrawler(BaseVideoCrawler):
    name = "youtube"
    url_prefixes = ("https://www.youtube.com",)

    def __init__(self):
        super().__init__(ChromeDriver(window_size = "300,1000").get_driver())

    def crawl_metadata(self):
        ydl_opts = {
            "quiet": True,
            "skip_download": True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
        # yt-dlp returns None for anything the video hides (like counts, subscriber
        # counts on some channels); the schema defaults cover those.
        self.metadata = VideoSchema(**{
            field: value
            for field, value in {
                "id":          info.get("id"),
                "title":       info.get("title"),
                "channel":     info.get("channel"),
                "channel_id":  info.get("channel_id"),
                "subscribers": info.get("channel_follower_count"),
                "description": info.get("description"),
                "date":        info.get("upload_date"),
                "views":       info.get("view_count"),
                "likes":       info.get("like_count"),
            }.items()
            if value is not None
        })

    def crawl_comments(
            self, 
            scroll_rounds: int = 1,
            reply_expansions: int = 3
        ):
        self.driver.get(self.url)
        time.sleep(2)

        for _ in range(scroll_rounds):
            self.driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
            time.sleep(2)

        # Bounded: clicking does not always clear the aria-label, so an unbounded
        # loop could spin forever on a video whose replies fail to expand.
        for _ in range(reply_expansions):
            try:
                buttons = self.driver.find_elements(
                    By.XPATH,
                    '//ytd-button-renderer//a[contains(@aria-label,"reply")]'
                )
            except WebDriverException as e:
                LOGGER.warning(f"video {self.metadata.id}: cannot list reply buttons: {e.msg}")
                break
            if not buttons:
                break
            for b in buttons:
                try:
                    self.driver.execute_script("arguments[0].click();", b)
                    time.sleep(2)
                except (WebDriverException, StaleElementReferenceException) as e:
                    LOGGER.debug(
                        f"video {self.metadata.id}: reply button skipped: {type(e).__name__}"
                    )

        try:
            threads = self.driver.find_elements(
                By.CSS_SELECTOR,
                "ytd-comment-thread-renderer"
            )
        except WebDriverException as e:
            LOGGER.warning(f"video {self.metadata.id}: cannot list comments: {e.msg}")
            return

        for e in threads:
            try:
                author = e.find_element(
                    By.CSS_SELECTOR, 
                    "#author-text"
                ).text
                text = e.find_element(
                    By.CSS_SELECTOR, 
                    "#content-text"
                ).text
                comment_data = CommentSchema(
                    author = author, 
                    comment = text, 
                    replies = []
                )

                replies = e.find_elements(By.CSS_SELECTOR, "ytd-comment-renderer #content-text")
                for r in replies[1:]:
                    try:
                        reply_author = r.find_element(
                            By.XPATH, 
                            "../../..//a[@id='author-text']"
                        ).text
                        reply_text = r.text
                        comment_data.replies.append(
                            SingleCommentSchema(
                                author = reply_author, 
                                comment = reply_text
                            ))
                    except (NoSuchElementException, StaleElementReferenceException):
                        # A reply row with no resolvable author is dropped, not fatal.
                        continue
                self.metadata.comments.append(comment_data)
            except (NoSuchElementException, StaleElementReferenceException,
                    ValidationError) as e:
                LOGGER.warning(
                    f"video {self.metadata.id}: skipped a comment, "
                    f"selectors likely stale ({type(e).__name__})"
                )

    def download_video(self):
        try:
            filename_template = f"{self.name}_{self.metadata.id}.%(ext)s"
            file_path = self.storage_path / filename_template
            ydl_opts = {
                "outtmpl": str(file_path),
                "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                "merge_output_format": "mp4",
                "quiet": True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])
            LOGGER.info(f"Saved video {self.metadata.id}")
        except (yt_dlp.utils.DownloadError, OSError) as e:
            LOGGER.error(f"video {self.metadata.id}: download failed ({type(e).__name__}: {e})")
