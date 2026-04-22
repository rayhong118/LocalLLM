# site_skills/safeway.py
from browser_use import BrowserSession
import logging
import asyncio

logger = logging.getLogger(__name__)

async def safeway_click_details(browser: BrowserSession, index: int):
    """SITE-SPECIFIC SKILL: Opens the 'Offer Details' popup for a coupon card, extracts
    the product name, deal price, and original price, then returns to the list.
    Returns a structured string: 'Name | Deal Price | Original Price'.

    Args:
        index: The index of the coupon card (0 for first, 1 for second, etc.)
    """
    page = await browser.get_current_page()
    try:
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

        # Wait for the detail page / popup to load
        await asyncio.sleep(2.5)

        # --- Extract data from the detail page ---
        name = ""
        deal_price = ""
        original_price = ""

        # Product name: try <h1>, then og:title, then page title
        try:
            h1 = page.locator("h1").first
            if await h1.count() > 0:
                name = (await h1.inner_text()).strip()
        except Exception:
            pass
        if not name:
            try:
                name = await page.title()
            except Exception:
                pass

        # Deal price: large price element in the coupon header
        deal_selectors = [
            "[class*='offer-price']", "[class*='deal-price']",
            "[class*='coupon-price']", "[class*='promo-price']",
            "[class*='save-price']", "[class*='badge-price']",
        ]
        for sel in deal_selectors:
            try:
                el = page.locator(sel).filter(visible=True).first
                if await el.count() > 0:
                    deal_price = (await el.inner_text()).strip()
                    break
            except Exception:
                continue

        # Original / in-store price
        orig_selectors = [
            "[class*='original-price']", "[class*='regular-price']",
            "[class*='instore-price']", "[class*='was-price']",
            "[class*='unit-price']",
        ]
        for sel in orig_selectors:
            try:
                el = page.locator(sel).filter(visible=True).first
                if await el.count() > 0:
                    original_price = (await el.inner_text()).strip()
                    break
            except Exception:
                continue

        # Fallback: grab first two price-like text nodes if specific selectors failed
        if not deal_price or not original_price:
            try:
                price_els = page.locator("text=/\\$[0-9]|[0-9]+¢/").filter(visible=True)
                prices = []
                for i in range(min(await price_els.count(), 4)):
                    txt = (await price_els.nth(i).inner_text()).strip()
                    if txt and txt not in prices:
                        prices.append(txt)
                if prices and not deal_price:
                    deal_price = prices[0]
                if len(prices) > 1 and not original_price:
                    original_price = prices[1]
            except Exception:
                pass

        # Navigate back to the list
        try:
            back_btn = page.locator("text=Back, [aria-label*='Back'], button:has-text('Back')").filter(visible=True).first
            if await back_btn.count() > 0:
                await back_btn.click()
            else:
                await page.go_back()
        except Exception:
            await page.go_back()

        await asyncio.sleep(1.5)

        result = f"Deal #{index}: Name='{name}' | Deal Price='{deal_price}' | Original Price='{original_price}'"
        return f"Success: {result}"

    except Exception as e:
        return f"Failure: Error in safeway_click_details: {str(e)}"


async def safeway_get_all_deals(browser: BrowserSession, keyword: str = ""):
    """SITE-SPECIFIC SKILL: Scrapes ALL visible coupon cards on the current page and returns
    a structured list of deals. Optionally filters by a keyword (e.g. 'sparkling water', 'ice cream').
    Call this after safeway_filter_category to get deal data in one step — no need to open each card.

    Args:
        keyword: Optional filter keyword to match against product names (case-insensitive).
    """
    page = await browser.get_current_page()
    try:
        await asyncio.sleep(1.5)  # Let any skeleton loaders resolve

        # Target coupon card containers
        card_selectors = [
            "[class*='coupon-card']", "[class*='offer-card']",
            "[class*='deal-card']", "[class*='product-card']",
            "article", "[role='article']",
        ]

        cards = None
        for sel in card_selectors:
            try:
                loc = page.locator(sel).filter(visible=True)
                if await loc.count() > 0:
                    cards = loc
                    break
            except Exception:
                continue

        if cards is None or await cards.count() == 0:
            return "Failure: No coupon cards found on the page. Try scrolling down or using safeway_filter_category first."

        deals = []
        total = await cards.count()
        for i in range(min(total, 30)):  # Cap at 30 to avoid token bloat
            try:
                card = cards.nth(i)
                text = (await card.inner_text()).strip().replace("\n", " | ")[:200]
                if keyword and keyword.lower() not in text.lower():
                    continue
                deals.append(f"Card {i}: {text}")
            except Exception:
                continue

        if not deals:
            return f"No deals matching '{keyword}' found among {total} visible cards."

        return "Found deals:\n" + "\n".join(deals)

    except Exception as e:
        return f"Failure: Error in safeway_get_all_deals: {str(e)}"


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

                # RETRY LOGIC: Safeway clicks often fail if the site is slow or skeleton.
                for attempt in range(3):
                    await target.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    await target.click()
                    await asyncio.sleep(2.0)

                    # Verify if it actually worked (check for checked state on target or its siblings)
                    is_checked = await target.locator("[aria-checked='true'], [class*='checked'], [class*='active']").count() > 0
                    if not is_checked:
                        # Fallback check for standard hidden input state if the label didn't show it
                        is_checked = await page.locator(f"//label[contains({xpath_translate}, '{target_lower}')]/preceding-sibling::input[@checked]").count() > 0

                    if is_checked:
                        return f"Success: Applied category filter for '{term}' after {attempt+1} attempts."

                    logger.warning(f"Filter click attempt {attempt+1} failed to toggle state for {term}. Retrying...")

                return f"Failure: Clicked '{term}' 3 times but the checkbox state did not change. The page might be frozen, or a modal might be blocking the click."

    except Exception as e:
        return f"Failure: Error applying filter for '{category_name}'. Sidebar or element may not exist yet or is hidden: {str(e)}"

    return f"Failure: Could not find any category filter matching keywords from '{category_name}'. Consider scrolling the page, opening a collapsed sidebar, or checking if the requested category actually exists on this page."

