from pydantic import BaseModel, Field
from typing import Optional, List

class SingleCommentSchema(BaseModel):
    author:  Optional[str] = ""
    comment: Optional[str] = ""

class CommentSchema(BaseModel):
    author:  Optional[str] = ""
    comment: Optional[str] = ""
    replies: Optional[List[SingleCommentSchema]] = Field(default_factory = list)

class VideoSchema(BaseModel):
    id:          Optional[str] = ""
    title:       Optional[str] = ""
    channel:     Optional[str] = ""
    channel_id:  Optional[str] = ""
    subscribers: Optional[int] = 0
    description: Optional[str] = ""
    date:        Optional[str] = ""
    views:       Optional[int] = 0
    likes:       Optional[int] = 0
    comments:    Optional[List[CommentSchema]] = Field(default_factory = list)
    hashtag:     Optional[List[str]] = Field(default_factory = list)