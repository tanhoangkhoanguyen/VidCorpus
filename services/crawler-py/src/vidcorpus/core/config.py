"""Runtime configuration, read from environment variables."""

import os
from pathlib import Path


class ConfigError(RuntimeError):
    """A required setting is missing or points at something unusable."""


def data_dir() -> Path:
    return Path(os.environ.get("VIDCORPUS_DATA_DIR", "data")).expanduser()


def required_path(env_var: str) -> Path:
    """Resolve env_var to an existing file, failing with the variable name."""
    raw = os.environ.get(env_var)
    if not raw:
        raise ConfigError(
            f"{env_var} is not set. Copy .env.example to .env and point it at your file."
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise ConfigError(f"{env_var} points at {path}, which does not exist.")
    return path
