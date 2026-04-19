# site_skills/safeway.py
from browser_use import BrowserSession
import logging

logger = logging.getLogger(__name__)

async def safeway_click_details(product_keyword: str, browser: BrowserSession):
    """SITE-SPECIFIC SKILL: On Safeway.com, use this to view the 'Offer Details' for a specific coupon card.
    
    Args:
        product_keyword: Specific text to identify the coupon (e.g., 'Talenti', 'Lucerne').
    """
    page = await browser.get_current_page()
    
    try:
        # 1. Target Locker Strategy: Find anchor text -> Find Card Boundary -> Click Button Inside
        # We look for the keyword, then find the nearest ancestor that likely represents the product card.
        # This prevents clicking "Program Details" in the header.
        
        # This XPath finds the text, then goes up to the card container, then finds the button inside.
        xpath = (
            f"//*(contains(text(), '{product_keyword}') or contains(@aria-label, '{product_keyword}'))"
            f"/ancestor::div[contains(@class, 'card') or contains(@class, 'offer') or contains(@class, 'product')][1]"
            f"//button[contains(., 'Details') or contains(., 'View')]"
        )
        
        locator = page.locator(xpath).filter(visible=True)
        
        if await locator.count() > 0:
            target = locator.first
            await target.scroll_into_view_if_needed()
            await target.click()
            return f"Success: Target-Locked and clicked details for '{product_keyword}'"
        
        # Fallback: Proximity search if card structure is unusual
        fallback_xpath = (
            f"//*(contains(text(), '{product_keyword}'))"
            f"/following::button[contains(., 'Details') or contains(., 'View')][1]"
        )
        locator = page.locator(fallback_xpath).filter(visible=True)
        if await locator.count() > 0:
            await locator.first.scroll_into_view_if_needed()
            await locator.first.click()
            return f"Success: Clicked details near text '{product_keyword}' (Proximity Fallback)"

    except Exception as e:
        return f"Failure: Error in safeway_click_details for '{product_keyword}': {str(e)}"

    return f"Failure: Could not 'Target-Lock' any coupon details for '{product_keyword}'. Please ensure the keyword matches a visible coupon title."

async def safeway_filter_category(category_name: str, browser: BrowserSession):
    """SITE-SPECIFIC SKILL: Use this to apply a category filter (e.g., 'Frozen Foods', 'Beverages') on Safeway.com.
    
    Args:
        category_name: The exact or partial name of the category to filter by (e.g., 'Frozen').
    """
    page = await browser.get_current_page()
    try:
        # Use case-insensitive XPath logic
        xpath_translate = 'translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")'
        
        # Try multiple variations of the input name
        search_terms = [category_name]
        # If multiple words, also try searching for individual words (fuzzy fallback)
        if " " in category_name:
            search_terms.extend([word for word in category_name.split() if len(word) > 3])
            
        for term in search_terms:
            target_lower = term.lower()
            
            # Priority 1: Label with the checkbox span
            xpath = (
                f"//label[contains({xpath_translate}, '{target_lower}')]"
                f"[descendant::span[contains(@class, 'checkbox-state')] or contains(@class, 'checkbox-state')]"
            )
            
            locator = page.locator(xpath).filter(visible=True)
            
            if await locator.count() == 0:
                # Priority 2: Any label containing the text
                xpath = f"//label[contains({xpath_translate}, '{target_lower}')]"
                locator = page.locator(xpath).filter(visible=True)

            if await locator.count() > 0:
                target = locator.first
                await target.scroll_into_view_if_needed()
                await asyncio.sleep(0.5) 
                
                # Check if already filtered
                is_checked = await target.locator("span[checkbox-state='checked']").count() > 0
                if is_checked:
                    return f"Success: Category matching '{term}' is already filtered."
                
                await target.click()
                await asyncio.sleep(1.0) 
                return f"Success: Clicked category filter for '{term}'"
        
    except Exception as e:
        return f"Failure: Error applying filter for '{category_name}': {str(e)}"

    return f"Failure: Could not find any category filter matching keywords from '{category_name}'"

        
    except Exception as e:
        return f"Failure: Error applying filter for '{category_name}': {str(e)}"

    return f"Failure: Could not find a category filter matching '{category_name}'"

