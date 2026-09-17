import json
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError

from vidcorpus.core.crawler import BaseVideoCrawler, scroll_until_more
from vidcorpus.core.log import get_logger
from vidcorpus.schema import CommentSchema, SingleCommentSchema

LOGGER = get_logger(__name__)


def _optional_int(value) -> int | None:
    """TikTok omits a count rather than sending 0 when a video hides it."""
    return None if value is None else int(value)
TIKTOK_HEADERS = {
    "accept": "*/*",
    "accept-language": "vi,en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",  # noqa: E501 - a user-agent must be one unbroken line
    "Referer": "https://www.tiktok.com/",
    "Accept-Encoding": "identity;q=1, *;q=0"
}
TIKTOK_COOKIES = {
    'tt_webid': '1' * 19,
    'tt_webid_v2': '1' * 19
}

#: Tall and narrow: comments render in a side rail, so width is wasted pixels.
COMMENT_RAIL_VIEWPORT = {"width": 400, "height": 1000}
#: One wheel notch per scroll round, in CSS pixels.
COMMENT_SCROLL_PIXELS = 4000


class TikTokVideoCrawler(BaseVideoCrawler):
    name = "tiktok"
    url_prefixes = ("https://vt.tiktok.com", "https://www.tiktok.com")
    required_selectors = {
        "rehydration_blob", "comment_button", "view_replies_button",
        "comment_wrapper", "comment_item", "comment_author", "comment_text",
        "reply_container", "reply_author", "reply_text",
    }
    #: The comment rail is a narrow column; a wide viewport just renders more chrome.
    viewport = COMMENT_RAIL_VIEWPORT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.video_download_url = None

    def _reset(self, url: str) -> None:
        super()._reset(url)
        self.video_download_url = None

    async def crawl_metadata(self) -> None:
        response = await self.http.get(
            self.url,
            headers = TIKTOK_HEADERS,
            cookies = TIKTOK_COOKIES,
        )
        self.metadata.id = re.search(r'/video/(\d+)', self.url).group(1)
        soup = BeautifulSoup(response.text, "html.parser")
        script_tag = self.selectors.select_one(soup, "rehydration_blob")
        if not script_tag:
            LOGGER.error(
                f"video {self.metadata.id}: no rehydration blob in the page - "
                "TikTok changed its markup, or this was a block page"
            )
            return

        try:
            data = json.loads(script_tag.string)
            item_struct = data['__DEFAULT_SCOPE__']['webapp.video-detail']['itemInfo']['itemStruct']
            text_tags = item_struct.get('textExtra', [])
            self.metadata.channel      = re.search(r'tiktok\.com/@([^/]+)/video', self.url).group(1)
            self.metadata.description  = re.sub(r'#\w+', '', item_struct.get('desc', '')).strip()
            # createTime is a Unix timestamp; the schema stores dates as strings.
            self.metadata.date         = datetime.fromtimestamp(
                int(item_struct['createTime']), tz = timezone.utc
            ).isoformat()
            # Absent counts stay None; 0 would claim the video has no likes.
            stats = item_struct.get('stats', {})
            self.metadata.likes        = _optional_int(stats.get('diggCount'))
            self.metadata.views        = _optional_int(stats.get('playCount'))
            self.metadata.hashtag      = [
                '#' + tag['hashtagName'] for tag in text_tags if tag.get('hashtagName')
            ]
            self.video_download_url = item_struct.get('video', {}).get('playAddr', '')
        except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError) as e:
            LOGGER.error(
                f"video {self.metadata.id}: cannot read the rehydration blob "
                f"({type(e).__name__}: {e})"
            )

    async def crawl_comments(self, scroll_rounds: int = 1, reply_expansions: int = 1) -> None:
        await self.page.goto(self.url, wait_until = "domcontentloaded")

        button = await self.selectors.wait_for(self.page, "comment_button")
        if button is None:
            LOGGER.warning(
                f"video {self.metadata.id}: comment button never appeared - "
                "comments stay empty"
            )
            return
        await button.click()

        await scroll_until_more(
            self.page, self.selectors, "comment_wrapper", scroll_rounds,
            lambda: self.page.mouse.wheel(0, COMMENT_SCROLL_PIXELS),
        )

        await self.__expand_replies(reply_expansions)

        html = await self.page.content()
        self.__parse_comments(BeautifulSoup(html, "html.parser"))

    async def __expand_replies(self, rounds: int) -> None:
        for _ in range(rounds):
            buttons = await self.selectors.all(self.page, "view_replies_button")
            if not buttons:
                return
            for button in buttons:
                try:
                    if "View" not in (await button.inner_text()).strip():
                        continue
                    await button.scroll_into_view_if_needed()
                    await button.click()
                except PlaywrightError as e:
                    # The rail re-renders as replies expand, so handles go stale.
                    LOGGER.debug(
                        f"video {self.metadata.id}: reply button skipped: {type(e).__name__}"
                    )

    def __parse_comments(self, soup: BeautifulSoup) -> None:
        """Pure parse over fetched HTML - no page access, so it is unit-testable."""
        for wrapper in self.selectors.select(soup, "comment_wrapper"):
            try:
                level1 = self.selectors.select_one(wrapper, "comment_item")
                if not level1:
                    continue

                author_tag = self.selectors.select_one(level1, "comment_author")
                text_tag = self.selectors.select_one(level1, "comment_text")
                comment = CommentSchema(
                    author = author_tag.get_text(strip = True) if author_tag else "",
                    comment = text_tag.get_text(strip = True) if text_tag else "",
                )

                reply_container = self.selectors.select_one(wrapper, "reply_container")
                if reply_container:
                    for reply in self.selectors.select(reply_container, "comment_item"):
                        author_2 = self.selectors.select_one(reply, "reply_author")
                        text_2 = self.selectors.select_one(reply, "reply_text")
                        if not (author_2 and text_2):
                            continue
                        # Replies nest an extra span layer; take the direct children.
                        spans = text_2.find_all('span', recursive = False)
                        body = " ".join(
                            s.get_text(strip = True) for s in spans if s.get_text(strip = True)
                        ) if spans else text_2.get_text(strip = True)
                        if body:
                            comment.replies.append(SingleCommentSchema(
                                author = author_2.get_text(strip = True),
                                comment = body,
                            ))

                self.metadata.comments.append(comment)
            except (AttributeError, TypeError, ValidationError) as e:
                LOGGER.warning(
                    f"video {self.metadata.id}: skipped a comment, "
                    f"selectors likely stale ({type(e).__name__}: {e})"
                )

    async def download_video(self) -> None:
        if not self.video_download_url:
            LOGGER.error(f"video {self.metadata.id}: no playAddr, nothing to download")
            return

        file_path = self.storage_path / f"{self.name}_{self.metadata.id}.mp4"
        # Copy: assigning into TIKTOK_HEADERS would leak Range into every later request.
        headers = {**TIKTOK_HEADERS, "Range": "bytes=0-"}

        try:
            async with self.http.stream(
                "GET", self.video_download_url, headers = headers, cookies = TIKTOK_COOKIES
            ) as response:
                response.raise_for_status()
                with open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
            LOGGER.info(f"Saved video {self.metadata.id}")
        except (httpx.HTTPError, OSError) as e:
            LOGGER.error(f"video {self.metadata.id}: download failed ({type(e).__name__}: {e})")
