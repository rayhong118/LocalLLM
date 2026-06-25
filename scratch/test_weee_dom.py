import asyncio
from browser_use import BrowserSession
import sys

async def main():
    session = BrowserSession(
        headless=True,
        channel="chrome",
        disable_security=True,
        user_data_dir=".browser_session_web",
        viewport={"width": 1280, "height": 720},
    )
    await session.start()
    page = await session.get_current_page()
    await page.goto("https://www.sayweee.com/en/on-sale")
    await asyncio.sleep(5)
    
    # Take a screenshot to visualize
    await page.screenshot(path="scratch/weee_onsale.png")
    print("Screenshot saved to scratch/weee_onsale.png")
    
    # Get all text content of elements that look like a sidebar/navigation or category filters
    links_info = await page.evaluate("""
    () => {
        const results = [];
        // Let's find all links and buttons that might be categories
        const elements = document.querySelectorAll('a, button, [role="link"], [role="button"]');
        elements.forEach(el => {
            const text = (el.innerText || el.textContent || '').trim();
            const href = el.getAttribute('href') || '';
            const aria = el.getAttribute('aria-label') || '';
            const className = el.className || '';
            if (text || aria) {
                results.push({text, href, aria, className});
            }
        });
        return results;
    }
    """)
    
    print(f"Found {len(links_info)} link/button elements.")
    # Filter for anything that could be category elements, e.g. containing category names or having specific classes
    for info in links_info[:150]:
        print(info)
        
    await session.stop()

if __name__ == "__main__":
    asyncio.run(main())
