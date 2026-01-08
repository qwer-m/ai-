import asyncio
from playwright.async_api import async_playwright, Browser

class BrowserPool:
    def __init__(self, max_instances: int = 5):
        self.max_instances = max_instances
        self.active_count = 0
        self.lock = asyncio.Lock()
        self.playwright = None
        self._browsers = [] # Track browser instances if needed, but we launch on demand usually?
        # Actually, if we want a pool, we should probably keep one browser instance and create contexts?
        # Or launch separate browsers? Separate browsers are safer for isolation but heavier.
        # User requested "Circuit Breaker" which implies limiting concurrency of LAUNCHING browsers.
        # So we launch on demand but limit the count.

    async def get_browser(self) -> Browser:
        async with self.lock:
            if self.active_count >= self.max_instances:
                raise Exception(f"Browser pool exhausted ({self.max_instances} max instances). Please try again later.")
            
            self.active_count += 1
            
        try:
            if not self.playwright:
                self.playwright = await async_playwright().start()
            
            # Launch a new browser instance
            # We use headless=True by default
            browser = await self.playwright.chromium.launch(headless=True)
            return browser
        except Exception as e:
            async with self.lock:
                self.active_count -= 1
            raise e

    async def release_browser(self, browser: Browser):
        if browser:
            await browser.close()
            async with self.lock:
                self.active_count -= 1

browser_pool = BrowserPool()
