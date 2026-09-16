"""Selectors live in YAML, not in code.

Providers change their markup constantly. Keeping selectors in a versioned file
means a break is fixed by editing data, and each selector can list fallbacks so
a rename degrades instead of failing outright. A fallback that actually fires is
logged at WARNING - that is the earliest signal the provider has changed.
"""

from datetime import date
from functools import cache
from pathlib import Path

import yaml
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field, ValidationError, field_validator
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from vidcorpus.core.config import ConfigError
from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)

_BY = {"css": By.CSS_SELECTOR, "xpath": By.XPATH, "tag": By.TAG_NAME}


class Selector(BaseModel):
    """One target, plus the alternatives to try if the primary is gone."""

    how: str = "css"
    query: str
    fallbacks: list[str] = Field(default_factory = list)

    @field_validator("how")
    @classmethod
    def known_strategy(cls, v: str) -> str:
        if v not in _BY:
            raise ValueError(f"how must be one of {sorted(_BY)}, got {v!r}")
        return v

    @property
    def candidates(self) -> list[str]:
        return [self.query, *self.fallbacks]

    @property
    def by(self) -> str:
        return _BY[self.how]


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

    def find_all(self, driver, key: str) -> list:
        """driver.find_elements for the first candidate that matches anything."""
        selector = self[key]
        for index, query in enumerate(selector.candidates):
            try:
                found = driver.find_elements(selector.by, query)
            except WebDriverException:
                continue
            if found:
                if index:
                    self._note_fallback(key, index, query)
                return found
        return []

    def find_one(self, driver, key: str):
        """driver.find_element for the first candidate that matches, else None."""
        found = self.find_all(driver, key)
        return found[0] if found else None

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

    def query(self, key: str) -> str:
        """The primary query string, for callers that build their own lookup."""
        return self[key].query

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
