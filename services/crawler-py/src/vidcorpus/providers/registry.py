"""Maps a platform name to its crawler pair, so callers never import providers directly."""

from vidcorpus.core.crawler import BaseLinksCrawler, BaseVideoCrawler
from vidcorpus.providers.tiktok.links import TikTokLinksCrawler
from vidcorpus.providers.tiktok.video import TikTokVideoCrawler
from vidcorpus.providers.youtube.links import YouTubeLinksCrawler
from vidcorpus.providers.youtube.video import YouTubeVideoCrawler

_LINKS = {c.name: c for c in (TikTokLinksCrawler, YouTubeLinksCrawler)}
_VIDEO = {c.name: c for c in (TikTokVideoCrawler, YouTubeVideoCrawler)}


def supported_platforms() -> list[str]:
    return sorted(_LINKS)


def _lookup(crawlers: dict, platform: str):
    try:
        return crawlers[platform]
    except KeyError:
        raise ValueError(
            f"Unknown platform {platform!r}. Available: {', '.join(supported_platforms())}"
        ) from None


def get_links_crawler(platform: str) -> type[BaseLinksCrawler]:
    return _lookup(_LINKS, platform)


def get_video_crawler(platform: str) -> type[BaseVideoCrawler]:
    return _lookup(_VIDEO, platform)
