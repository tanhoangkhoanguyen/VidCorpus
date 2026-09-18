"""The selector layer: validation, fallbacks, and the warning that matters."""

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from vidcorpus.core.config import ConfigError
from vidcorpus.core.selectors import SelectorSet, load
from vidcorpus.providers import registry

SRC = Path(__file__).parent.parent / "src" / "vidcorpus"


def make_set(**selectors) -> SelectorSet:
    return SelectorSet.model_validate({
        "platform": "test", "version": 1, "last_verified": "2026-09-03",
        "selectors": selectors,
    })


class TestValidation:
    def test_rejects_an_unknown_strategy(self):
        with pytest.raises(ValueError, match="how must be one of"):
            make_set(a = {"how": "jquery", "query": "div"})

    def test_a_missing_file_is_a_config_error(self):
        with pytest.raises(ConfigError, match="no selectors.yaml"):
            load("no_such_platform")


class TestFallbacks:
    def test_the_primary_wins_and_stays_quiet(self, caplog):
        selectors = make_set(c = {"query": ".first", "fallbacks": [".second"]})
        soup = BeautifulSoup('<b class="first">A</b><b class="second">B</b>', "html.parser")

        assert selectors.select_one(soup, "c").text == "A"
        assert caplog.text == ""

    def test_a_fallback_matches_and_warns(self, caplog):
        selectors = make_set(c = {"query": ".gone", "fallbacks": [".stale", ".here"]})
        soup = BeautifulSoup('<b class="here">A</b>', "html.parser")

        assert selectors.select_one(soup, "c").text == "A"
        # This warning is the early signal a provider changed its markup.
        assert "fallback #2 matched" in caplog.text
        assert "likely changed its markup" in caplog.text

    def test_nothing_matching_is_none_not_an_error(self):
        selectors = make_set(c = {"query": ".gone", "fallbacks": [".also-gone"]})
        soup = BeautifulSoup("<p>nothing</p>", "html.parser")

        assert selectors.select_one(soup, "c") is None
        assert selectors.select(soup, "c") == []


class TestRequire:
    def test_names_every_missing_key(self):
        selectors = make_set(a = {"query": "div"})
        with pytest.raises(ConfigError, match="missing: b, c"):
            selectors.require({"a", "b", "c"})


class TestProviderFiles:
    """The YAML and the code that reads it must agree - checked, not assumed."""

    @pytest.fixture(params = registry.supported_platforms())
    def platform(self, request):
        return request.param

    def test_validate_config_accepts_the_shipped_file(self, platform):
        # TikTok's links crawler also wants credentials, so only the video
        # crawler's config is checkable without an environment.
        registry.get_video_crawler(platform).validate_config()

    def test_every_declared_key_exists_in_the_yaml(self, platform):
        keys = set(load(platform).selectors)
        for crawler in (registry.get_links_crawler(platform),
                        registry.get_video_crawler(platform)):
            assert crawler.required_selectors <= keys, (
                f"{crawler.__name__} declares keys the YAML does not define"
            )

    def test_every_key_the_code_uses_is_declared(self, platform):
        """Catches the failure mode of adding a lookup and forgetting to declare it.

        An undeclared key still loads, then raises mid-crawl instead of at
        startup - exactly what required_selectors exists to prevent.
        """
        used = set()
        for module in ("links", "video"):
            source = (SRC / "providers" / platform / f"{module}.py").read_text(
                encoding = "utf-8"
            )
            used |= set(re.findall(
                r'selectors\.(?:all|locate|wait_for|select|select_one|query)'
                r'\([^,]+,\s*"([a-z_]+)"',
                source,
            ))
            used |= set(re.findall(r'selectors\.query\("([a-z_]+)"\)', source))

        declared = (registry.get_links_crawler(platform).required_selectors
                    | registry.get_video_crawler(platform).required_selectors)
        assert used <= declared, f"used but not declared: {sorted(used - declared)}"

    def test_no_selector_is_defined_but_unused(self, platform):
        declared = (registry.get_links_crawler(platform).required_selectors
                    | registry.get_video_crawler(platform).required_selectors)
        assert set(load(platform).selectors) - declared == set()


#: A DOM lookup whose argument is a literal string. Lookups fed from
#: self.selectors.query(...) are fine - the string still comes from the YAML.
LITERAL_DOM_LOOKUP = re.compile(
    r"\.(?:select|select_one|find|find_all|locator|query_selector(?:_all)?)"
    r"\(\s*[\"'](?P<query>[^\"']+)[\"']"
)
#: A bare HTML tag name is not a platform selector - <span> is <span> forever.
HTML_TAG = re.compile(r"^[a-z][a-z0-9]*$")


def test_no_literal_selector_survives_in_provider_code():
    """Selectors belong in YAML, reachable only through SelectorSet.

    Matching on selector *spelling* would miss whatever the next hardcoded one
    happens to be spelled like, so this matches on the shape of the call: a DOM
    lookup given a literal query string. Passing self.selectors.query(...) is
    allowed, since that string still came from the YAML, and so is a bare tag
    name, which no provider can rename.
    """
    offenders = []
    for path in (SRC / "providers").rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding = "utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            match = LITERAL_DOM_LOOKUP.search(line)
            if match and not HTML_TAG.match(match.group("query")):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "hardcoded selectors: " + "; ".join(offenders)
