import config
from browser_use import BrowserSession
from stealth import inject_stealth, cleanup_dom, inject_plan_banner, inject_stall_banner, remove_stall_banner

class ManagedBrowser:
    def __init__(self):
        self.session = BrowserSession(
            headless=False,
            channel="chrome",
            disable_security=True,
            minimum_wait_page_load_time=config.BROWSER_WAIT_TIME,
            wait_for_network_idle_page_load_time=config.BROWSER_WAIT_TIME,
            wait_between_actions=0.8,
            user_data_dir=".browser_session_web",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            window_size={"width": 1280, "height": 720},
            cross_origin_iframes=False,
            max_iframes=3,
            max_iframe_depth=1,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--mute-audio",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
        )

    async def start(self):
        await inject_stealth(self.session)
        await self.session.start()
        page = await self.session.get_current_page()
        if page:
            await page.goto("about:blank")
            return True
        return False

    async def stop(self):
        if self.session:
            await self.session.stop()

    async def prepare_step(self):
        """Standard preparations before every agent step."""
        await inject_stealth(self.session)
        await cleanup_dom(self.session)

    async def inject_plan(self, step: int, description: str, total: int):
        await inject_plan_banner(self.session, step, description, total)

    async def inject_stall(self, message: str):
        await inject_stall_banner(self.session, message)

    async def clear_stall(self):
        await remove_stall_banner(self.session)

    def get_session(self) -> BrowserSession:
        return self.session
