# skills.py
from browser_use import Controller, BrowserSession
import logging

logger = logging.getLogger(__name__)
controller = Controller()

@controller.action('smart_search')
async def smart_search(query: str, browser: BrowserSession):
    """Finds a search input on the page, types the query, and hits Enter.
    
    Args:
        query: The search term to enter
    """
    page = await browser.get_current_page()
    
    # Common search input selectors
    selectors = [
        'input[type="search"]',
        'input[name="q"]',
        'input[placeholder*="Search" i]',
        'input[aria-label*="Search" i]',
        'input[id*="search" i]'
    ]
    
    for selector in selectors:
        try:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                await element.fill(query)
                await page.keyboard.press('Enter')
                return f"Successfully searched for '{query}' using selector: {selector}"
        except Exception:
            continue
            
    return f"Failed to find a visible search bar for query: {query}"

@controller.action('click_element_by_text')
async def click_element_by_text(text: str, browser: BrowserSession):
    """Finds a clickable element (button, link) by its visible text and clicks it.
    
    Args:
        text: The exact visible text of the element to click
    """
    page = await browser.get_current_page()
    
    try:
        # Use Playwright's text locator
        locator = page.get_by_text(text, exact=True)
        if await locator.count() > 0:
            await locator.first.click()
            return f"Successfully clicked element with text: '{text}'"
    except Exception as e:
        return f"Error clicking element with text '{text}': {str(e)}"
        
    return f"Could not find a clickable element with text: '{text}'"
