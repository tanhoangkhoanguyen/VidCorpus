"""The storage layer, against a real Postgres started by testcontainers.

Not mocked: the behaviour under test is upserts, partial unique indexes and
transactions, none of which a fake would reproduce.
"""

import pytest

pytest.importorskip("testcontainers.postgres")

from testcontainers.postgres import PostgresContainer  # noqa: E402

from vidcorpus.schema import CommentSchema, SingleCommentSchema, VideoSchema  # noqa: E402
from vidcorpus.storage.database import migrate  # noqa: E402
from vidcorpus.storage.repository import Repository  # noqa: E402

pytestmark = pytest.mark.integration

TABLES = ("videos", "channels", "comments", "hashtags", "video_hashtags",
          "video_stats_history", "crawl_jobs")


@pytest.fixture(scope = "session")
def postgres_url():
    """One container for the whole session; each test truncates instead."""
    with PostgresContainer("postgres:16-alpine", driver = None) as container:
        yield container.get_connection_url()


@pytest.fixture
async def pool(postgres_url):
    import asyncpg
    pool = await asyncpg.create_pool(postgres_url, min_size = 1, max_size = 4)
    await migrate(pool)
    await pool.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest.fixture
def repo(pool):
    return Repository(pool)


def make_video(vid = "v1", views = 100, likes = 10, comments = None):
    return VideoSchema(
        id = vid, title = "A video", channel = "alice", channel_id = "UC123",
        subscribers = 500, description = "desc",
        date = "2024-12-10T03:06:40+00:00",
        views = views, likes = likes, hashtag = ["#travel", "#food"],
        comments = [
            CommentSchema(author = "bob", comment = "nice", replies = [
                SingleCommentSchema(author = "carol", comment = "agreed"),
            ]),
            CommentSchema(author = "dave", comment = "cool"),
        ] if comments is None else comments,
    )


class TestMigrations:
    async def test_rerunning_is_a_no_op(self, pool):
        # A second migrate must not re-apply 001 and wipe the corpus.
        assert await migrate(pool) == await migrate(pool)


class TestSaveVideo:
    async def test_records_provenance(self, repo, pool):
        video_id = await repo.save_video(
            "tiktok", make_video(), "https://tt/1", selectors_version = 3
        )
        row = await pool.fetchrow(
            "SELECT parser_version, selectors_version, crawled_at FROM videos WHERE id=$1",
            video_id,
        )
        assert row["parser_version"]
        assert row["selectors_version"] == 3
        assert row["crawled_at"]

    @pytest.mark.parametrize("date,expected_year", [
        ("2024-12-10T03:06:40+00:00", 2024),   # TikTok, ISO
        ("20240115", 2024),                     # yt-dlp, YYYYMMDD
    ])
    async def test_parses_both_date_formats(self, repo, pool, date, expected_year):
        video = make_video()
        video.date = date
        video_id = await repo.save_video("tiktok", video, "https://tt/1")
        stored = await pool.fetchval(
            "SELECT published_at FROM videos WHERE id=$1", video_id
        )
        assert stored.year == expected_year

    async def test_an_unparseable_date_stores_null(self, repo, pool):
        video = make_video()
        video.date = "sometime last tuesday"
        video_id = await repo.save_video("tiktok", video, "https://tt/1")
        assert await pool.fetchval(
            "SELECT published_at FROM videos WHERE id=$1", video_id
        ) is None

    async def test_the_same_id_on_another_platform_is_another_video(self, repo, pool):
        await repo.save_video("tiktok", make_video("shared"), "https://tt/1")
        await repo.save_video("youtube", make_video("shared"), "https://yt/1")
        assert await pool.fetchval("SELECT count(*) FROM videos") == 2


class TestRecrawl:
    async def test_updates_in_place(self, repo, pool):
        first = await repo.save_video("tiktok", make_video(views = 100), "https://tt/1")
        second = await repo.save_video("tiktok", make_video(views = 250), "https://tt/1")

        assert first == second
        assert await pool.fetchval("SELECT count(*) FROM videos") == 1
        assert await pool.fetchval("SELECT views FROM videos WHERE id=$1", first) == 250

    async def test_history_keeps_every_observation(self, repo, pool):
        # The point of append-only: the 100 is gone from videos.views, but
        # nothing can recrawl what the count used to be, so it is kept here.
        await repo.save_video("tiktok", make_video(views = 100), "https://tt/1")
        await repo.save_video("tiktok", make_video(views = 250), "https://tt/1")
        await repo.save_video("tiktok", make_video(views = 400), "https://tt/1")

        rows = await pool.fetch(
            "SELECT views FROM video_stats_history ORDER BY id"
        )
        assert [r["views"] for r in rows] == [100, 250, 400]

    async def test_does_not_duplicate_comments(self, repo, pool):
        await repo.save_video("tiktok", make_video(), "https://tt/1")
        await repo.save_video("tiktok", make_video(), "https://tt/1")
        assert await pool.fetchval("SELECT count(*) FROM comments") == 3

    async def test_does_not_duplicate_hashtags(self, repo, pool):
        await repo.save_video("tiktok", make_video(), "https://tt/1")
        await repo.save_video("tiktok", make_video("v2"), "https://tt/2")
        assert await pool.fetchval("SELECT count(*) FROM hashtags") == 2
        assert await pool.fetchval("SELECT count(*) FROM video_hashtags") == 4


class TestComments:
    async def test_replies_point_at_their_parent(self, repo, pool):
        await repo.save_video("tiktok", make_video(), "https://tt/1")
        row = await pool.fetchrow(
            """SELECT c.author AS child, p.author AS parent
                 FROM comments c JOIN comments p ON p.id = c.parent_comment_id"""
        )
        assert (row["parent"], row["child"]) == ("bob", "carol")

    async def test_two_authors_saying_the_same_thing_are_two_rows(self, repo, pool):
        video = make_video(comments = [
            CommentSchema(author = "x", comment = "same"),
            CommentSchema(author = "y", comment = "same"),
        ])
        await repo.save_video("tiktok", video, "https://tt/1")
        assert await pool.fetchval("SELECT count(*) FROM comments") == 2

    async def test_the_same_text_under_different_videos_is_kept(self, repo, pool):
        comments = [CommentSchema(author = "x", comment = "first!")]
        await repo.save_video("tiktok", make_video("v1", comments = comments), "https://tt/1")
        await repo.save_video("tiktok", make_video("v2", comments = comments), "https://tt/2")
        assert await pool.fetchval("SELECT count(*) FROM comments") == 2


class TestCrawlJobs:
    async def test_a_new_url_can_be_claimed(self, repo):
        assert await repo.claim_job("tiktok", "https://tt/1") is True

    async def test_a_finished_url_is_refused(self, repo):
        await repo.claim_job("tiktok", "https://tt/1")
        await repo.finish_job("tiktok", "https://tt/1")
        assert await repo.claim_job("tiktok", "https://tt/1") is False

    async def test_a_failed_url_can_be_retried(self, repo, pool):
        await repo.claim_job("tiktok", "https://tt/1")
        await repo.finish_job("tiktok", "https://tt/1", error = "timeout")

        assert await repo.claim_job("tiktok", "https://tt/1") is True
        assert await pool.fetchval(
            "SELECT attempts FROM crawl_jobs WHERE url='https://tt/1'"
        ) == 2

    async def test_finished_urls_are_the_resume_set(self, repo):
        for url in ("https://tt/1", "https://tt/2", "https://tt/3"):
            await repo.claim_job("tiktok", url)
        await repo.finish_job("tiktok", "https://tt/1")
        await repo.finish_job("tiktok", "https://tt/2", error = "boom")

        assert await repo.known_urls("tiktok") == {"https://tt/1"}

    async def test_known_ids_are_scoped_to_the_platform(self, repo):
        await repo.save_video("tiktok", make_video("t1"), "https://tt/1")
        await repo.save_video("youtube", make_video("y1"), "https://yt/1")

        assert await repo.known_video_ids("tiktok") == {"t1"}
        assert await repo.known_video_ids("youtube") == {"y1"}


