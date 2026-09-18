"""One shape for every provider.

Fields a platform does not publish stay None: TikTok has no subscriber count,
and None says "unknown" where 0 would claim the channel has no followers.
"""

from pydantic import BaseModel, Field


class SingleCommentSchema(BaseModel):
    author: str = ""
    comment: str = ""


class CommentSchema(BaseModel):
    author: str = ""
    comment: str = ""
    replies: list[SingleCommentSchema] = Field(default_factory = list)


class VideoSchema(BaseModel):
    #: Always present - every provider identifies a video and its channel.
    id: str = ""
    channel: str = ""
    description: str = ""
    date: str = ""
    comments: list[CommentSchema] = Field(default_factory = list)

    #: YouTube only. TikTok videos have no title and no channel id.
    title: str | None = None
    channel_id: str | None = None
    subscribers: int | None = None

    #: TikTok only. YouTube tags are not scraped.
    hashtag: list[str] | None = None

    #: Both platforms, but either can hide these on a given video.
    views: int | None = None
    likes: int | None = None
