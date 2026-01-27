"""
浏览器进程池模块 (Browser Pool Module)

管理 Playwright 浏览器实例的生命周期。
实现简单的连接池机制，限制并发浏览器实例数量，防止资源耗尽。
主要用于 UI 自动化测试任务。
"""

import asyncio
from playwright.async_api import async_playwright, Browser

class BrowserPool:
    """
    浏览器进程池 (Browser Pool)
    
    控制浏览器实例的创建和复用 (或并发限制)。
    """
    def __init__(self, max_instances: int = 5):
        """
        初始化浏览器池。
        
        Args:
            max_instances: 最大并发浏览器实例数。
        """
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
        """
        获取一个浏览器实例 (Get Browser)
        
        如果当前活跃实例数未达到上限，则启动新浏览器。
        否则抛出异常 (或等待，当前实现为抛出异常)。
        
        Returns:
            Browser: Playwright Browser 实例。
        """
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
        """
        释放浏览器实例 (Release Browser)
        
        关闭浏览器并减少活跃计数。
        
        Args:
            browser: 要释放的 Browser 实例。
        """
        if browser:
            await browser.close()
            async with self.lock:
                self.active_count -= 1

browser_pool = BrowserPool()
