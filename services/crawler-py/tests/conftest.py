import json
from pathlib import Path

import pytest

from vidcorpus.core.selectors import load
from vidcorpus.providers.tiktok.video import TikTokVideoCrawler
from vidcorpus.schema import VideoSchema

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html():
    """Read a saved page, e.g. fixture_html("tiktok", "video_normal.html")."""
    def read(platform: str, name: str) -> str:
        return (FIXTURES / platform / name).read_text(encoding = "utf-8")
    return read


@pytest.fixture
def fixture_json():
    def read(platform: str, name: str) -> dict:
        return json.loads((FIXTURES / platform / name).read_text(encoding = "utf-8"))
    return read


@pytest.fixture
def tiktok_video():
    """A TikTok crawler with only the parsing half wired up.

    __init__ wants a browser context and an HTTP client; the parsers need
    neither, so the object is built without them.
    """
    crawler = TikTokVideoCrawler.__new__(TikTokVideoCrawler)
    crawler.selectors = load("tiktok")
    crawler.metadata = VideoSchema()
    return crawler
