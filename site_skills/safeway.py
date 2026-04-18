# site_skills/safeway.py
from browser_use import BrowserSession
import logging

logger = logging.getLogger(__name__)

async def safeway_click_details(product_keyword: str, browser: BrowserSession):
    """Safeway-specific skill to find a coupon card by keyword and click its 'Details' button.
    
    Args:
        product_keyword: Keyword to identify the coupon card (e.g., 'Ice Cream').
    """
    page = await browser.get_current_page()
    
    try:
        # 1. Find the coupon container that contains the keyword
        # Coupons usually have a common class or role. On Safeway, they are often in 'div.product-card' or similar.
        # We'll use a broad search for a container with the keyword.
        xpath = f"//div[contains(., '{product_keyword}')]//button[contains(., 'Details') or contains(., 'View')]"
        locator = page.locator(xpath).filter(visible=True)
        
        if await locator.count() > 0:
            await locator.first.scroll_into_view_if_needed()
            await locator.first.click()
            return f"Success: Clicked Safeway Details button for product matching '{product_keyword}'"
        
        # Fallback 2: Try looking for the text and then the nearest button
        xpath_fallback = f"//*[contains(text(), '{product_keyword}')]/ancestor::div[contains(@class, 'card')]//button"
        locator = page.locator(xpath_fallback).filter(visible=True)
        for i in range(await locator.count()):
            btn = locator.nth(i)
            text = await btn.inner_text()
            if "Details" in text or "View" in text:
                await btn.scroll_into_view_if_needed()
                await btn.click()
                return f"Success: Clicked Safeway Details button (fallback) for '{product_keyword}'"

    except Exception as e:
        return f"Failure: Error in safeway_click_details for '{product_keyword}': {str(e)}"

    return f"Failure: Could not find Safeway coupon or Details button for '{product_keyword}'"
