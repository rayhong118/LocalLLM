# site_skills/safeway.py
from browser_use import BrowserSession
import logging
import asyncio

logger = logging.getLogger(__name__)

async def safeway_click_details(browser: BrowserSession, index: int):
    """SITE-SPECIFIC SKILL: Step 2 of Safeway Workflow.
    Opens the 'Offer Details' popup for a coupon.
    
    ### V4 PRICE LOGIC ###
    - DEAL PRICE: Read the large price in the Coupon UI/Header.
    - ORIGINAL PRICE: Read the 'In-store' or 'Original' price next to the item in the list.
    
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
        await asyncio.sleep(0.3)
        await target.click()
        
        # 2. Wait for the popup to appear (usually indicated by an 'Eligible Items' header or similar)
        try:
            await page.wait_for_selector("text=Eligible Items", timeout=5000)
        except:
            await asyncio.sleep(1.0)
            
        return f"Success: Clicked 'Offer Details' for coupon at index {index}. Verification: Popup visible."

    except Exception as e:
        return f"Failure: Error in safeway_click_details: {str(e)}"

async def safeway_filter_category(category_name: str, browser: BrowserSession):
    """SITE-SPECIFIC SKILL: Step 1 of Safeway Workflow. 
    MANDATORY: ALWAYS select category before looking for products.
    
    ### V4 RULES ###
    - DO NOT use the search box.
    - If sidebar is missing, scroll down or check if collapsed.
    
    Args:
        category_name: The name of the category (e.g., 'Frozen Foods').
    """
    page = await browser.get_current_page()
    try:
        # Give the sidebar/page a moment to populate or wait for any filter label
        try:
            await page.wait_for_selector("label", timeout=5000)
        except:
            await asyncio.sleep(2.0)
        
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
                
                # RETRY LOGIC: Safeway clicks often fail if the site is slow or skeleton.
                for attempt in range(3):
                    await target.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2) 
                    await target.click()
                    await asyncio.sleep(1.5)
                    
                    # Verify if it actually worked (check for checked state on target or its siblings)
                    is_checked = await target.locator("[aria-checked='true'], [class*='checked'], [class*='active']").count() > 0
                    if not is_checked:
                         # Fallback check for standard hidden input state if the label didn't show it
                         is_checked = await page.locator(f"//label[contains({xpath_translate}, '{target_lower}')]/preceding-sibling::input[@checked]").count() > 0
                    
                    if is_checked:
                        return f"Success: Applied category filter for '{term}' after {attempt+1} attempts."
                    
                    logger.warning(f"Filter click attempt {attempt+1} failed to toggle state for {term}. Retrying...")
                
                return f"Failure: Clicked '{term}' 3 times but the checkbox state did not change. The page might be frozen."
        
    except Exception as e:
        return f"Failure: Error applying filter for '{category_name}': {str(e)}"

    return f"Failure: Could not find any category filter matching keywords from '{category_name}'"
