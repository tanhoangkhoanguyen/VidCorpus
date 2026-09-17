"""Selectors live in YAML, not in code.

Markup churn is fixed by editing data. Each entry may list fallbacks, and a
fallback firing logs a WARNING - the earliest signal the provider changed.
"""

from datetime import date
from functools import cache
from pathlib import Path

import yaml
from bs4 import BeautifulSoup, Tag
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from pydantic import BaseModel, Field, ValidationError, field_validator

from vidcorpus.core.config import ConfigError
from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)

#: Playwright takes one selector string; xpath needs an explicit engine prefix.
_PREFIX = {"css": "", "xpath": "xpath="}


class Selector(BaseModel):
    """One target, plus the alternatives to try if the primary is gone."""

    how: str = "css"
    query: str
    fallbacks: list[str] = Field(default_factory = list)

    @field_validator("how")
    @classmethod
    def known_strategy(cls, v: str) -> str:
        if v not in _PREFIX:
            raise ValueError(f"how must be one of {sorted(_PREFIX)}, got {v!r}")
        return v

    @property
    def candidates(self) -> list[str]:
        return [self.query, *self.fallbacks]

    def engine(self, query: str) -> str:
        """The query as Playwright wants it."""
        return _PREFIX[self.how] + query


class SelectorSet(BaseModel):
    """One provider's selector file, and the lookups that use it."""

    platform: str
    version: int
    last_verified: date
    selectors: dict[str, Selector]

    def __getitem__(self, key: str) -> Selector:
        try:
            return self.selectors[key]
        except KeyError:
            raise KeyError(
                f"{self.platform} has no selector named {key!r}; "
                f"file defines: {', '.join(sorted(self.selectors))}"
            ) from None

    def _note_fallback(self, key: str, index: int, query: str) -> None:
        LOGGER.warning(
            f"{self.platform}.{key}: primary selector failed, fallback #{index} matched "
            f"({query!r}) - {self.platform} has likely changed its markup"
        )

    # --- live page (Playwright) ---

    async def locate(self, root: Page | Locator, key: str) -> Locator | None:
        """The first candidate that matches at least one element, else None."""
        selector = self[key]
        for index, query in enumerate(selector.candidates):
            locator = root.locator(selector.engine(query))
            try:
                if await locator.count():
                    if index:
                        self._note_fallback(key, index, query)
                    return locator
            except PlaywrightError:
                # A malformed query must not take down the crawl - try the next.
                continue
        return None

    async def all(self, root: Page | Locator, key: str) -> list[Locator]:
        """Every element the first matching candidate finds."""
        locator = await self.locate(root, key)
        return await locator.all() if locator else []

    async def wait_for(self, root: Page | Locator, key: str,
                       timeout: float = 10_000) -> Locator | None:
        """Wait for the first candidate to become visible, else None.

        Replaces fixed sleeps: returns the moment the element appears. The
        primary gets the full timeout, fallbacks a short probe each.
        """
        selector = self[key]
        for index, query in enumerate(selector.candidates):
            locator = root.locator(selector.engine(query)).first
            try:
                await locator.wait_for(state = "visible",
                                       timeout = timeout if index == 0 else 1_000)
            except PlaywrightError:
                continue
            if index:
                self._note_fallback(key, index, query)
            return locator
        return None

    # --- fetched HTML (BeautifulSoup) ---

    def select(self, soup: BeautifulSoup | Tag, key: str) -> list[Tag]:
        """BeautifulSoup .select for the first candidate that matches anything."""
        selector = self[key]
        for index, query in enumerate(selector.candidates):
            found = soup.select(query)
            if found:
                if index:
                    self._note_fallback(key, index, query)
                return found
        return []

    def select_one(self, soup: BeautifulSoup | Tag, key: str) -> Tag | None:
        found = self.select(soup, key)
        return found[0] if found else None

    # --- misc ---

    def query(self, key: str) -> str:
        """The primary query as Playwright wants it, for callers that locate directly."""
        selector = self[key]
        return selector.engine(selector.query)

    def require(self, keys: set[str]) -> None:
        """Raise ConfigError if any key the crawler uses is absent.

        Called from validate_config so an incomplete file fails at startup rather
        than part-way through a crawl.
        """
        missing = sorted(keys - set(self.selectors))
        if missing:
            raise ConfigError(
                f"{self.platform}/selectors.yaml is missing: {', '.join(missing)}"
            )


@cache
def load(platform: str) -> SelectorSet:
    """Read and validate providers/<platform>/selectors.yaml.

    Raises ConfigError rather than failing mid-crawl, so a malformed or
    incomplete file surfaces before any browser is launched.
    """
    path = Path(__file__).parent.parent / "providers" / platform / "selectors.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding = "utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"{platform} has no selectors.yaml at {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping, got {type(raw).__name__}")

    try:
        return SelectorSet.model_validate({**raw, "platform": platform})
    except ValidationError as e:
        raise ConfigError(f"{path} is malformed: {e}") from e
