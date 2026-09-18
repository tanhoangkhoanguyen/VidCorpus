"""YouTube metadata parsing, offline. Saved yt-dlp info dicts in, VideoSchema out."""

from vidcorpus.providers.youtube.video import YouTubeVideoCrawler

parse = YouTubeVideoCrawler.parse_metadata


def test_reads_an_ordinary_video(fixture_json):
    video = parse(fixture_json("youtube", "info_normal.json"))

    assert video.id == "dQw4w9WgXcQ"
    assert video.title == "Lovestruck in the City - Piano Cover"
    assert video.channel_id == "UCabcdefghijklmnop123456"
    assert video.subscribers == 84_300
    assert (video.views, video.likes) == (1_204_553, 45_231)
    assert video.date == "20240115"


def test_hidden_counts_stay_none(fixture_json):
    # yt-dlp returns None for anything a video hides. Those keys are dropped
    # so the field keeps its default - None, meaning "not published", which
    # is not the same claim as 0.
    video = parse(fixture_json("youtube", "info_hidden_counts.json"))

    assert video.id == "hiddenVid01"
    assert (video.likes, video.subscribers) == (None, None)
    assert video.channel_id is None
    assert video.description == ""
    assert video.views == 42  # a published count is still kept


def test_a_nearly_empty_info_dict_parses(fixture_json):
    video = parse(fixture_json("youtube", "info_minimal.json"))
    assert video.id == "bareVideo1"
    assert video.title is None
    assert video.views is None


def test_ignores_fields_no_column_reads(fixture_json):
    # duration, categories and tags are in the input and simply not stored;
    # parsing must not trip over them.
    info = fixture_json("youtube", "info_normal.json")
    assert info["duration"] == 253 and info["categories"] == ["Music"]

    video = parse(info)
    assert video.id == "dQw4w9WgXcQ"


def test_does_not_mutate_the_input(fixture_json):
    info = fixture_json("youtube", "info_normal.json")
    before = dict(info)
    parse(info)
    assert info == before
