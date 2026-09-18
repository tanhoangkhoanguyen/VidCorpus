import asyncio

import yt_dlp
from playwright.async_api import Error as PlaywrightError

from vidcorpus.core.crawler import BaseVideoCrawler
from vidcorpus.core.log import get_logger
from vidcorpus.schema import CommentSchema, SingleCommentSchema, VideoSchema

LOGGER = get_logger(__name__)

#: Comments live below the fold and load lazily, so scroll before waiting.
COMMENT_SCROLL_ROUNDS = 3
#: Each comment row is already on screen by the time we read it; a long wait
#: here would only slow down threads whose author link is genuinely missing.
FIELD_TIMEOUT = 2_000
#: Replies animate in after a click.
REPLY_RENDER_WAIT = 0.5

#: yt-dlp field -> VideoSchema field.
METADATA_FIELDS = {
    "id": "id",
    "title": "title",
    "channel": "channel",
    "channel_id": "channel_id",
    "channel_follower_count": "subscribers",
    "description": "description",
    "upload_date": "date",
    "view_count": "views",
    "like_count": "likes",
}


class YouTubeVideoCrawler(BaseVideoCrawler):
    name = "youtube"
    url_prefixes = ("https://www.youtube.com",)
    required_selectors = {
        "reply_button", "comment_thread", "comment_author", "comment_text",
        "reply_text", "reply_author_from_text",
    }

    async def crawl_metadata(self) -> None:
        """Fetch, then hand the info dict to the pure parser."""
        # yt-dlp is synchronous and network-bound; keep it off the event loop.
        info = await asyncio.to_thread(self.__extract_info)
        self.metadata = self.parse_metadata(info)

    @staticmethod
    def parse_metadata(info: dict) -> VideoSchema:
        """yt-dlp info dict in, metadata out. No I/O.

        Static and side-effect free, so a saved info dict tests it offline.
        """
        # yt-dlp returns None for anything the video hides (like counts,
        # subscriber counts on some channels); the schema defaults cover those.
        video = VideoSchema(**{
            field: info.get(source)
            for source, field in METADATA_FIELDS.items()
            if info.get(source) is not None
        })
        return video

    def __extract_info(self) -> dict:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            return ydl.extract_info(self.url, download = False)

    async def crawl_comments(self, scroll_rounds: int = 1, reply_expansions: int = 3) -> None:
        await self.page.goto(self.url, wait_until = "domcontentloaded")

        # Comments sit below the fold; nudge the page until threads appear.
        for _ in range(max(scroll_rounds, 1) * COMMENT_SCROLL_ROUNDS):
            await self.page.keyboard.press("End")
            if await self.selectors.wait_for(
                self.page, "comment_thread", timeout = FIELD_TIMEOUT
            ):
                break
        else:
            LOGGER.info(f"video {self.metadata.id}: no comments")
            return

        await self.__expand_replies(reply_expansions)

        for thread in await self.selectors.all(self.page, "comment_thread"):
            comment = await self.__parse_thread(thread)
            if comment:
                self.metadata.comments.append(comment)

    async def __expand_replies(self, rounds: int) -> None:
        # Bounded: clicking does not always clear the aria-label, so an unbounded
        # loop could spin forever on a video whose replies fail to expand.
        for _ in range(rounds):
            buttons = await self.selectors.all(self.page, "reply_button")
            if not buttons:
                return
            for button in buttons:
                try:
                    await button.click()
                except PlaywrightError as e:
                    LOGGER.debug(
                        f"video {self.metadata.id}: reply button skipped: {type(e).__name__}"
                    )
            await asyncio.sleep(REPLY_RENDER_WAIT)

    async def __parse_thread(self, thread) -> CommentSchema | None:
        try:
            author = await self.selectors.wait_for(
                thread, "comment_author", timeout = FIELD_TIMEOUT
            )
            text = await self.selectors.wait_for(
                thread, "comment_text", timeout = FIELD_TIMEOUT
            )
            if not (author and text):
                return None

            comment = CommentSchema(
                author = (await author.inner_text()).strip(),
                comment = (await text.inner_text()).strip(),
            )

            replies = await self.selectors.all(thread, "reply_text")
            # The first match is the parent comment's own text.
            for reply in replies[1:]:
                try:
                    author_locator = reply.locator(
                        self.selectors.query("reply_author_from_text")
                    ).first
                    comment.replies.append(SingleCommentSchema(
                        author = (await author_locator.inner_text()).strip(),
                        comment = (await reply.inner_text()).strip(),
                    ))
                except PlaywrightError:
                    # A reply row with no resolvable author is dropped, not fatal.
                    continue
            return comment
        except PlaywrightError as e:
            LOGGER.warning(
                f"video {self.metadata.id}: skipped a comment, "
                f"selectors likely stale ({type(e).__name__})"
            )
            return None

    async def download_video(self) -> None:
        await asyncio.to_thread(self.__download)

    def __download(self) -> None:
        try:
            outtmpl = str(self.storage_path / f"{self.name}_{self.metadata.id}.%(ext)s")
            with yt_dlp.YoutubeDL({
                "outtmpl": outtmpl,
                "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                "merge_output_format": "mp4",
                "quiet": True,
            }) as ydl:
                ydl.download([self.url])
            LOGGER.info(f"Saved video {self.metadata.id}")
        except (yt_dlp.utils.DownloadError, OSError) as e:
            LOGGER.error(f"video {self.metadata.id}: download failed ({type(e).__name__}: {e})")
