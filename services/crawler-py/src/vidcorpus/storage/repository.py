"""Reads and writes the corpus. Raw SQL, one transaction per video.

Idempotent: a recrawl updates the video in place but still appends to
video_stats_history, because counts that moved cannot be recovered later.
"""

from datetime import datetime, timezone

import asyncpg

from vidcorpus.core.log import get_logger
from vidcorpus.schema import VideoSchema

LOGGER = get_logger(__name__)

#: Bumped when the parse changes shape, so rows can be re-derived selectively.
PARSER_VERSION = "1"


def _parse_timestamp(value: str | None) -> datetime | None:
    """Providers hand back ISO strings (TikTok) or YYYYMMDD (yt-dlp)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo = timezone.utc)
    except ValueError:
        LOGGER.warning(f"unparseable date {value!r}, storing NULL")
        return None


class Repository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    # -- read ---------------------------------------------------------------

    async def known_video_ids(self, platform: str) -> set[str]:
        """Platform ids already stored, so a restart does not recrawl them."""
        rows = await self.pool.fetch(
            "SELECT platform_video_id FROM videos WHERE platform = $1", platform
        )
        return {r["platform_video_id"] for r in rows}

    async def known_urls(self, platform: str) -> set[str]:
        """URLs already finished, for dedupe across runs."""
        rows = await self.pool.fetch(
            "SELECT url FROM crawl_jobs WHERE platform = $1 AND status = 'done'",
            platform,
        )
        return {r["url"] for r in rows}

    # -- write --------------------------------------------------------------

    async def claim_job(self, platform: str, url: str) -> bool:
        """Register the URL as being worked on. False if it is already done.

        ON CONFLICT makes this safe to call from several workers at once.
        """
        # Inside DO UPDATE, `crawl_jobs.status` is the pre-update value, so the
        # RETURNING expression sees what the row was before this claim.
        was_done = await self.pool.fetchval(
            """
            INSERT INTO crawl_jobs (platform, url, status, attempts)
            VALUES ($1, $2, 'running', 1)
            ON CONFLICT (platform, url) DO UPDATE
                SET status     = CASE WHEN crawl_jobs.status = 'done'
                                      THEN 'done'::crawl_status
                                      ELSE 'running'::crawl_status END,
                    attempts   = crawl_jobs.attempts + 1,
                    updated_at = now()
            RETURNING (crawl_jobs.status = 'done')
            """,
            platform, url,
        )
        return not was_done

    async def finish_job(self, platform: str, url: str, error: str | None = None) -> None:
        await self.pool.execute(
            """
            UPDATE crawl_jobs
               SET status     = $3::crawl_status,
                   last_error = $4,
                   updated_at = now()
             WHERE platform = $1 AND url = $2
            """,
            platform, url, "failed" if error else "done", error,
        )

    async def save_video(
            self,
            platform: str,
            video: VideoSchema,
            url: str,
            selectors_version: int | None = None,
        ) -> int:
        """Upsert one video with its channel, comments and hashtags.

        One transaction: a crawl either lands whole or not at all.
        """
        async with self.pool.acquire() as conn, conn.transaction():
            channel_id = await self.__upsert_channel(conn, platform, video)
            video_id = await self.__upsert_video(
                conn, platform, video, url, channel_id, selectors_version,
            )
            await self.__append_stats(conn, video_id, video)
            await self.__save_comments(conn, video_id, video)
            await self.__save_hashtags(conn, video_id, video)
            return video_id

    async def __upsert_channel(self, conn, platform: str, video: VideoSchema) -> int | None:
        # TikTok gives a handle, YouTube a channel id; whichever exists identifies it.
        platform_channel_id = video.channel_id or video.channel
        if not platform_channel_id:
            return None
        return await conn.fetchval(
            """
            INSERT INTO channels (platform, platform_channel_id, handle, name, subscribers)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (platform, platform_channel_id) DO UPDATE
                SET name        = coalesce(excluded.name, channels.name),
                    handle      = coalesce(excluded.handle, channels.handle),
                    subscribers = coalesce(excluded.subscribers, channels.subscribers),
                    crawled_at  = now()
            RETURNING id
            """,
            platform, platform_channel_id, video.channel, video.channel,
            video.subscribers or None,
        )

    async def __upsert_video(
            self, conn, platform: str, video: VideoSchema, url: str,
            channel_id: int | None, selectors_version: int | None,
        ) -> int:
        return await conn.fetchval(
            """
            INSERT INTO videos (
                platform, platform_video_id, url, channel_id,
                title, description, published_at, views, likes,
                parser_version, selectors_version
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (platform, platform_video_id) DO UPDATE
                SET url                 = excluded.url,
                    channel_id          = coalesce(excluded.channel_id, videos.channel_id),
                    title               = coalesce(excluded.title, videos.title),
                    description         = coalesce(excluded.description, videos.description),
                    published_at        = coalesce(excluded.published_at, videos.published_at),
                    views               = excluded.views,
                    likes               = excluded.likes,
                    parser_version      = excluded.parser_version,
                    selectors_version   = excluded.selectors_version,
                    crawled_at          = now()
            RETURNING id
            """,
            platform, video.id, url, channel_id,
            video.title or None, video.description or None,
            _parse_timestamp(video.date), video.views, video.likes,
            PARSER_VERSION, selectors_version,
        )

    async def __append_stats(self, conn, video_id: int, video: VideoSchema) -> None:
        # Append-only: never an UPDATE. Counts that moved cannot be recrawled.
        await conn.execute(
            """
            INSERT INTO video_stats_history (video_id, views, likes, comments)
            VALUES ($1, $2, $3, $4)
            """,
            video_id, video.views, video.likes, len(video.comments),
        )

    async def __save_comments(self, conn, video_id: int, video: VideoSchema) -> None:
        for comment in video.comments:
            parent_id = await conn.fetchval(
                """
                INSERT INTO comments (video_id, parent_comment_id, author, body)
                VALUES ($1, NULL, $2, $3)
                ON CONFLICT (video_id, author, md5(body))
                    WHERE parent_comment_id IS NULL
                    DO UPDATE SET crawled_at = now()
                RETURNING id
                """,
                video_id, comment.author, comment.comment,
            )
            for reply in comment.replies:
                await conn.execute(
                    """
                    INSERT INTO comments (video_id, parent_comment_id, author, body)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (video_id, parent_comment_id, author, md5(body))
                        WHERE parent_comment_id IS NOT NULL
                        DO UPDATE SET crawled_at = now()
                    """,
                    video_id, parent_id, reply.author, reply.comment,
                )

    async def __save_hashtags(self, conn, video_id: int, video: VideoSchema) -> None:
        for tag in video.hashtag:
            hashtag_id = await conn.fetchval(
                """
                INSERT INTO hashtags (tag) VALUES ($1)
                ON CONFLICT (tag) DO UPDATE SET tag = excluded.tag
                RETURNING id
                """,
                tag,
            )
            await conn.execute(
                """
                INSERT INTO video_hashtags (video_id, hashtag_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                video_id, hashtag_id,
            )
