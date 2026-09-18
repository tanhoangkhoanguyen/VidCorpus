"""One Chromium process, many isolated browser contexts.

Selenium ran one Chrome per worker, which is where the memory went. Contexts
share the process but not cookies, so worker count is no longer bound by RAM.
"""

from contextlib import asynccontextmanager

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from vidcorpus.core.log import get_logger

LOGGER = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
)

# Same intent as the old Chrome options: do not look like automation.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
]

#: Wide enough that desktop layouts render, small enough not to waste raster work.
DEFAULT_VIEWPORT = {"width": 1280, "height": 900}

# navigator.webdriver is true under automation; sites check it.
_STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


class BrowserPool:
    """Owns the browser process. Hand out contexts, never the browser itself."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless = self.headless,
            args = LAUNCH_ARGS,
        )
        LOGGER.info(f"Chromium started (headless={self.headless})")

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None

    async def new_context(
            self,
            viewport: dict | None = None,
            cookies: list[dict] | None = None
        ) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("BrowserPool.start() has not been awaited")
        context = await self._browser.new_context(
            user_agent = USER_AGENT,
            viewport = viewport or DEFAULT_VIEWPORT,
            locale = "vi-VN",
        )
        await context.add_init_script(_STEALTH)
        if cookies:
            await context.add_cookies(cookies)
        return context

    @asynccontextmanager
    async def page(self, **kwargs) -> Page:
        """A page in its own context, both closed on exit."""
        context = await self.new_context(**kwargs)
        try:
            yield await context.new_page()
        finally:
            await context.close()

    async def __aenter__(self) -> "BrowserPool":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()
