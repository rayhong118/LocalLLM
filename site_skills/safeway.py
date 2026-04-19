# site_skills/safeway.py
from browser_use import BrowserSession
import logging
import asyncio

logger = logging.getLogger(__name__)

@skill
async def safeway_click_details(browser: Browser, index: int):
    """SITE-SPECIFIC SKILL: Step 2 of Safeway Workflow.
    Find and click the 'Offer Details' (or 'View Details') link for a specific coupon card by index.
    This opens the popup showing 'Qualifying Product(s)'.
    
    Args:
        index: The index of the coupon card (0 for first, 1 for second, etc.)
    """
    page = await browser.get_current_page()
    try:
        # 1. Target specifically the 'Offer Details' links
        # Safeway usually has 'cardOfferDetails_XXX' IDs for these.
        xpath = "//a[contains(., 'Details') or contains(@id, 'OfferDetails') or contains(@aria-label, 'Details')]"
        locator = page.locator(xpath).filter(visible=True)
        
        count = await locator.count()
        if count == 0:
            return "Failure: No 'Offer Details' links found on the current page."
        
        if index >= count:
            return f"Failure: Requested index {index} is out of bounds (only {count} coupons found)."
            
        target = locator.nth(index)
        await target.scroll_into_view_if_needed()
        await asyncio.sleep(0.5)
        await target.click()
        
        # 2. Wait for the popup to appear
        await asyncio.sleep(2.0)
        return f"Success: Clicked 'Offer Details' for coupon at index {index}."

    except Exception as e:
        return f"Failure: Error in safeway_click_details: {str(e)}"

async def safeway_filter_category(category_name: str, browser: BrowserSession):
    """SITE-SPECIFIC SKILL: Step 1 of Safeway Workflow. MANDATORY: Select the category first. 
    
    DO NOT use the search box. Select category, then find candidate coupons in the resulting list.
    
    Args:
        category_name: The exact or partial name of the category to filter by.
    """
    page = await browser.get_current_page()
    try:
        # Give the sidebar/page a moment to populate
        await asyncio.sleep(3.0)
        
        # Use case-insensitive XPath logic
        xpath_translate = 'translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")'
        
        # Try multiple variations of the input name
        search_terms = [category_name]
        # If multiple words, also try searching for individual words (fuzzy fallback)
        if " " in category_name:
            search_terms.extend([word for word in category_name.split() if len(word) > 3])
            
        for term in search_terms:
            target_lower = term.lower()
            
            # Priority 1: Label specifically within a sidebar/filter container
            xpath = (
                f"//*[contains(@class, 'filter') or contains(@class, 'sidebar') or contains(@id, 'filter')]"
                f"//label[contains({xpath_translate}, '{target_lower}')]"
            )
            
            locator = page.locator(xpath).filter(visible=True)
            
            if await locator.count() == 0:
                # Priority 2: Fallback to any label (standard Safeway sidebar structure)
                xpath = f"//label[contains({xpath_translate}, '{target_lower}')]"
                locator = page.locator(xpath).filter(visible=True)

            if await locator.count() > 0:
                target = locator.first
                await target.scroll_into_view_if_needed()
                await asyncio.sleep(0.5) 
                
                # Check for visual indicators of selection (e.g., aria-checked or specific classes)
                is_checked = await target.locator("[aria-checked='true'], [class*='checked'], [class*='active']").count() > 0
                if is_checked:
                    return f"Success: Category matching '{term}' is already filtered (Detected via ARIA/Class)."
                
                await target.click()
                # Wait for content to potentially shift/reload
                await asyncio.sleep(2.0) 
                return f"Success: Applied category filter for '{term}'"
        
    except Exception as e:
        return f"Failure: Error applying filter for '{category_name}': {str(e)}"

    return f"Failure: Could not find any category filter matching keywords from '{category_name}'"
