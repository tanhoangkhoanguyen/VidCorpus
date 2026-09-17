-- Initial corpus schema.
--
-- The dataset has no committed use case yet, so this leans on three rules:
--   1. keep the raw provider response, including fields nothing reads today
--   2. record provenance, so any row can be traced to when and how it was made
--   3. append-only for anything that moves, because history cannot be recrawled
--
-- Video blobs stay on disk; this holds metadata and the text.

BEGIN;

CREATE TABLE schema_version (
    version     integer     PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------------------
-- channels
-- --------------------------------------------------------------------------

CREATE TABLE channels (
    id                  bigserial   PRIMARY KEY,
    platform            text        NOT NULL,
    -- The provider's own id where we get one (YouTube), else the handle.
    platform_channel_id text        NOT NULL,
    handle              text,
    name                text,
    subscribers         bigint,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    crawled_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT channels_platform_id_key UNIQUE (platform, platform_channel_id)
);

-- --------------------------------------------------------------------------
-- videos
-- --------------------------------------------------------------------------

CREATE TABLE videos (
    id                bigserial   PRIMARY KEY,
    platform          text        NOT NULL,
    platform_video_id text        NOT NULL,
    url               text        NOT NULL,
    channel_id        bigint      REFERENCES channels(id) ON DELETE SET NULL,

    title             text,
    description       text,
    published_at      timestamptz,

    -- Current values. History lives in video_stats_history; these are a
    -- convenience for "latest" queries, not the record of what changed.
    views             bigint,
    likes             bigint,

    -- Provenance: which parse produced this row, and when.
    parser_version    text        NOT NULL,
    selectors_version integer,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    crawled_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT videos_platform_id_key UNIQUE (platform, platform_video_id)
);

CREATE INDEX videos_published_at_idx      ON videos (published_at DESC NULLS LAST);
CREATE INDEX videos_platform_published_idx ON videos (platform, published_at DESC NULLS LAST);
CREATE INDEX videos_channel_idx           ON videos (channel_id);

-- --------------------------------------------------------------------------
-- video_stats_history: append-only
-- --------------------------------------------------------------------------
--
-- Views and likes move. Overwriting them destroys the only copy of what they
-- were, and no recrawl can bring that back. Every crawl appends a row here.

CREATE TABLE video_stats_history (
    id          bigserial   PRIMARY KEY,
    video_id    bigint      NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    views       bigint,
    likes       bigint,
    comments    bigint,
    observed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX video_stats_history_video_idx ON video_stats_history (video_id, observed_at DESC);

-- --------------------------------------------------------------------------
-- comments
-- --------------------------------------------------------------------------

CREATE TABLE comments (
    id                bigserial   PRIMARY KEY,
    video_id          bigint      NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    -- Neither provider exposes a stable comment id through this path, so
    -- identity is (video, author, text) - see the unique index below.
    parent_comment_id bigint      REFERENCES comments(id) ON DELETE CASCADE,
    author            text        NOT NULL DEFAULT '',
    body              text        NOT NULL DEFAULT '',
    crawled_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX comments_video_idx  ON comments (video_id);
CREATE INDEX comments_parent_idx ON comments (parent_comment_id);

-- Recrawling a video must not duplicate its comments. NULLs never compare
-- equal, so top-level comments need their own partial index.
CREATE UNIQUE INDEX comments_dedupe_root_idx
    ON comments (video_id, author, md5(body))
    WHERE parent_comment_id IS NULL;
CREATE UNIQUE INDEX comments_dedupe_reply_idx
    ON comments (video_id, parent_comment_id, author, md5(body))
    WHERE parent_comment_id IS NOT NULL;

-- --------------------------------------------------------------------------
-- hashtags
-- --------------------------------------------------------------------------

CREATE TABLE hashtags (
    id  bigserial PRIMARY KEY,
    tag text      NOT NULL UNIQUE
);

CREATE TABLE video_hashtags (
    video_id   bigint NOT NULL REFERENCES videos(id)   ON DELETE CASCADE,
    hashtag_id bigint NOT NULL REFERENCES hashtags(id) ON DELETE CASCADE,
    PRIMARY KEY (video_id, hashtag_id)
);

CREATE INDEX video_hashtags_hashtag_idx ON video_hashtags (hashtag_id);

-- --------------------------------------------------------------------------
-- crawl_jobs: what has been attempted, so a restart can skip it
-- --------------------------------------------------------------------------

CREATE TYPE crawl_status AS ENUM ('pending', 'running', 'done', 'failed');

CREATE TABLE crawl_jobs (
    id           bigserial    PRIMARY KEY,
    platform     text         NOT NULL,
    url          text         NOT NULL,
    status       crawl_status NOT NULL DEFAULT 'pending',
    attempts     integer      NOT NULL DEFAULT 0,
    last_error   text,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT crawl_jobs_url_key UNIQUE (platform, url)
);

CREATE INDEX crawl_jobs_status_idx ON crawl_jobs (status, created_at);

INSERT INTO schema_version (version) VALUES (1);

COMMIT;
