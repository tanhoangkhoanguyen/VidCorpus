"""TikTok parsing, offline. Saved pages in, VideoSchema out."""

import pytest

URL = "https://www.tiktok.com/@traveler/video/7401234567890123456"


class TestMetadata:
    def test_reads_an_ordinary_video(self, tiktok_video, fixture_html):
        video, download = tiktok_video.parse_metadata(
            fixture_html("tiktok", "video_normal.html"), URL
        )

        assert video.id == "7401234567890123456"
        assert video.channel == "traveler"
        assert video.views == 1_204_553
        assert video.likes == 45_231
        assert download == "https://v16.tiktokcdn.com/abc/video.mp4"

    def test_strips_hashtags_out_of_the_description(self, tiktok_video, fixture_html):
        video, _ = tiktok_video.parse_metadata(
            fixture_html("tiktok", "video_normal.html"), URL
        )
        assert video.description == "Đà Nẵng 3 ngày 2 đêm"
        assert video.hashtag == ["#travel", "#danang", "#food"]

    def test_converts_createTime_to_an_iso_string(self, tiktok_video, fixture_html):
        # The schema stores dates as text; createTime arrives as an int, and
        # assigning it directly used to raise part-way through the parse.
        video, _ = tiktok_video.parse_metadata(
            fixture_html("tiktok", "video_normal.html"), URL
        )
        assert video.date.startswith("2024-12-10T")

    def test_hidden_counts_stay_none(self, tiktok_video, fixture_html):
        # None, not 0: the video hides the count, it does not have none.
        video, download = tiktok_video.parse_metadata(
            fixture_html("tiktok", "video_no_stats.html"),
            "https://www.tiktok.com/@quiet/video/7409999999999999999",
        )
        assert (video.views, video.likes) == (None, None)
        assert video.hashtag == []
        assert download  # still downloadable

    def test_removed_video_yields_an_empty_record(self, tiktok_video, fixture_html, caplog):
        video, download = tiktok_video.parse_metadata(
            fixture_html("tiktok", "video_removed.html"), URL
        )
        # The id comes from the URL, so it survives even with no page data.
        assert video.id == "7401234567890123456"
        assert (video.views, download) == (None, "")
        assert "no rehydration blob" in caplog.text

    def test_changed_blob_shape_is_reported_not_raised(
            self, tiktok_video, fixture_html, caplog):
        video, download = tiktok_video.parse_metadata(
            fixture_html("tiktok", "video_shape_changed.html"), URL
        )
        assert download == ""
        assert "cannot read the rehydration blob" in caplog.text

    def test_a_url_without_a_video_id_does_not_raise(self, tiktok_video, fixture_html):
        video, _ = tiktok_video.parse_metadata(
            fixture_html("tiktok", "video_normal.html"), "https://www.tiktok.com/@x"
        )
        assert video.id == ""


class TestComments:
    def test_reads_threads_and_their_replies(self, tiktok_video, fixture_html):
        comments = tiktok_video.parse_comments(
            fixture_html("tiktok", "comments_normal.html")
        )

        assert [c.author for c in comments] == ["alice", "carol", "dave"]
        assert comments[0].comment == "Quán này ở đâu vậy bạn?"
        assert [(r.author, r.comment) for r in comments[0].replies] == [
            ("traveler", "Ở Sơn Trà nha"),
            ("bob", "mình cũng muốn biết"),
        ]

    def test_a_page_with_no_comments_yields_none(self, tiktok_video, fixture_html):
        assert tiktok_video.parse_comments(
            fixture_html("tiktok", "comments_empty.html")
        ) == []

    def test_renamed_hooks_yield_empty_fields_not_a_crash(
            self, tiktok_video, fixture_html):
        # The wrapper still matches, the inner data-e2e hooks do not. This is
        # what a partial markup change looks like.
        comments = tiktok_video.parse_comments(
            fixture_html("tiktok", "comments_stale_selectors.html")
        )
        assert len(comments) == 1
        assert (comments[0].author, comments[0].comment) == ("", "")


@pytest.mark.parametrize("name", [
    "video_normal.html", "video_no_stats.html",
    "video_removed.html", "video_shape_changed.html",
])
def test_no_fixture_makes_the_parser_raise(tiktok_video, fixture_html, name):
    """Whatever the page, a parse failure is logged, never propagated."""
    tiktok_video.parse_metadata(fixture_html("tiktok", name), URL)


def test_half_rendered_threads_drop_what_is_missing(tiktok_video, fixture_html):
    """A thread that rendered incompletely must lose only the missing parts.

    The fixture holds three wrappers: one with no body, one whose reply has no
    author, and one whose reply text is not wrapped in the usual inner span.
    """
    comments = tiktok_video.parse_comments(
        fixture_html("tiktok", "comments_partial.html")
    )
    assert [c.author for c in comments] == ["alice", "bob"]      # bodyless one skipped
    assert next(c for c in comments if c.author == "alice").replies == []
    assert [(r.author, r.comment)
            for r in next(c for c in comments if c.author == "bob").replies] == [
        ("carol", "no inner span here"),
    ]
