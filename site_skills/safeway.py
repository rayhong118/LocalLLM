# site_skills/safeway.py
# Rewritten to use CDP-compatible page.evaluate() instead of Playwright locators.
# browser-use's Page object is a CDP wrapper — it does NOT have .locator(), .get_by_text(), etc.

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
        logger.info(f"[safeway_click_details] Starting extraction for deal index {index}")
        # Find and click the Details link at the given index using JS
        click_result = await page.evaluate("""
        (idx) => {
            const links = Array.from(document.querySelectorAll('a')).filter(a => {
                const text = a.textContent || '';
                const label = a.getAttribute('aria-label') || '';
                const id = a.id || '';
                return /details/i.test(text) || /details/i.test(label) || /OfferDetails/i.test(id);
            }).filter(a => a.offsetParent !== null);
            
            if (links.length === 0) return JSON.stringify({error: 'No Offer Details links found'});
            if (idx >= links.length) return JSON.stringify({error: 'Index ' + idx + ' out of bounds (only ' + links.length + ' found)'});
            
            links[idx].scrollIntoView({block: 'center'});
            links[idx].click();
            return JSON.stringify({success: true, total: links.length});
        }
        """, index)

        result_data = __import__('json').loads(click_result)
        if 'error' in result_data:
            logger.warning(f"[safeway_click_details] Failed to click link: {result_data['error']}")
            return f"Failure: {result_data['error']}"

        logger.info(f"[safeway_click_details] Successfully clicked 'Offer Details' link. Waiting for popup...")
        # Wait for the detail page / popup to load
        await asyncio.sleep(2.5)

        # Extract data from the detail page using JS
        extracted = await page.evaluate("""
        () => {
            let name = '';
            let dealPrice = '';
            let originalPrice = '';
            
            // Product name: try h1, then og:title, then page title
            const h1 = document.querySelector('h1');
            if (h1 && h1.textContent.trim()) {
                name = h1.textContent.trim();
            } else {
                name = document.title || '';
            }
            
            // Deal price selectors
            const dealSelectors = [
                '[class*="offer-price"]', '[class*="deal-price"]',
                '[class*="coupon-price"]', '[class*="promo-price"]',
                '[class*="save-price"]', '[class*="badge-price"]'
            ];
            for (const sel of dealSelectors) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null && el.textContent.trim()) {
                    dealPrice = el.textContent.trim();
                    break;
                }
            }
            
            // Original price selectors
            const origSelectors = [
                '[class*="original-price"]', '[class*="regular-price"]',
                '[class*="instore-price"]', '[class*="was-price"]',
                '[class*="unit-price"]'
            ];
            for (const sel of origSelectors) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null && el.textContent.trim()) {
                    originalPrice = el.textContent.trim();
                    break;
                }
            }
            
            // Fallback: grab price-like text nodes
            if (!dealPrice || !originalPrice) {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const prices = [];
                while (walker.nextNode()) {
                    const txt = walker.currentNode.textContent.trim();
                    if (/\\$[0-9]|[0-9]+¢/.test(txt) && txt.length < 20) {
                        prices.push(txt);
                        if (prices.length >= 4) break;
                    }
                }
                if (prices.length > 0 && !dealPrice) dealPrice = prices[0];
                if (prices.length > 1 && !originalPrice) originalPrice = prices[1];
            }
            
            return JSON.stringify({name, dealPrice, originalPrice});
        }
        """)

        data = __import__('json').loads(extracted)
        logger.info(f"[safeway_click_details] Extracted data: Name='{data.get('name')}', Deal='{data.get('dealPrice')}', Orig='{data.get('originalPrice')}'")

        # Navigate back to the list
        try:
            back_result = await page.evaluate("""
            () => {
                const backBtn = document.querySelector(
                    '[aria-label*="Back"], button:has(> *:only-child)'
                );
                const backLink = Array.from(document.querySelectorAll('a, button')).find(
                    el => /^back$/i.test(el.textContent.trim())
                );
                const target = backBtn || backLink;
                if (target) {
                    target.click();
                    return 'clicked';
                }
                return 'not_found';
            }
            """)
            if back_result == 'not_found':
                await page.go_back()
        except Exception:
            await page.go_back()

        await asyncio.sleep(1.5)

        result = f"Deal #{index}: Name='{data.get('name','')}' | Deal Price='{data.get('dealPrice','')}' | Original Price='{data.get('originalPrice','')}'"
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
        logger.info(f"[safeway_get_all_deals] Scraping all deals. Keyword filter: '{keyword}'")
        await asyncio.sleep(1.5)  # Let any skeleton loaders resolve

        # Scrape all card-like elements using JS
        raw = await page.evaluate("""
        (kw) => {
            const selectors = [
                '[class*="coupon-card"]', '[class*="offer-card"]',
                '[class*="deal-card"]', '[class*="product-card"]',
                'article', '[role="article"]'
            ];
            
            let cards = [];
            for (const sel of selectors) {
                try {
                    const els = Array.from(document.querySelectorAll(sel))
                        .filter(el => el.offsetParent !== null);
                    if (els.length > 0) {
                        cards = els;
                        break;
                    }
                } catch(e) {}
            }
            
            if (cards.length === 0) {
                return JSON.stringify({error: 'No coupon cards found on the page.'});
            }
            
            const kwLower = (kw || '').toLowerCase();
            const deals = [];
            const limit = Math.min(cards.length, 30);
            
            for (let i = 0; i < limit; i++) {
                try {
                    let text = cards[i].innerText.trim().replace(/\\n/g, ' | ').substring(0, 200);
                    if (kwLower && text.toLowerCase().indexOf(kwLower) === -1) continue;
                    deals.push('Card ' + i + ': ' + text);
                } catch(e) {}
            }
            
            return JSON.stringify({total: cards.length, deals: deals});
        }
        """, keyword)

        data = __import__('json').loads(raw)
        
        if 'error' in data:
            return f"Failure: {data['error']} Try scrolling down or using safeway_filter_category first."

        deals = data.get('deals', [])
        total = data.get('total', 0)

        if not deals:
            logger.warning(f"[safeway_get_all_deals] No deals matching '{keyword}' found among {total} cards.")
            return f"No deals matching '{keyword}' found among {total} visible cards."

        logger.info(f"[safeway_get_all_deals] Successfully found {len(deals)} deals matching keyword out of {total} total cards.")
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
        logger.info(f"[safeway_filter_category] Starting filter selection for category: '{category_name}'")
        # Give the sidebar/page a moment to populate
        await asyncio.sleep(3.0)

        # Build search terms — try full name first, then individual words
        search_terms = [category_name]
        if " " in category_name:
            search_terms.extend([word for word in category_name.split() if len(word) > 3])

        for term in search_terms:
            logger.info(f"[safeway_filter_category] Trying to find and click label for term: '{term}'")
            # Click and verify in one go to ensure we click the exact same element
            click_result = await page.evaluate("""
            (targetText) => {
                const targetLower = targetText.toLowerCase();
                
                // Find the best label
                const filterContainers = document.querySelectorAll(
                    '[class*="filter"], [class*="sidebar"], [id*="filter"]'
                );
                
                let targetLabel = null;
                
                for (const container of filterContainers) {
                    const labels = container.querySelectorAll('label, [role="checkbox"]');
                    for (const label of labels) {
                        if ((label.textContent || '').toLowerCase().includes(targetLower) && label.offsetParent !== null) {
                            targetLabel = label;
                            break;
                        }
                    }
                    if (targetLabel) break;
                }
                
                if (!targetLabel) {
                    const allLabels = document.querySelectorAll('label, [role="checkbox"]');
                    for (const label of allLabels) {
                        if ((label.textContent || '').toLowerCase().includes(targetLower) && label.offsetParent !== null) {
                            targetLabel = label;
                            break;
                        }
                    }
                }
                
                if (!targetLabel) {
                    return JSON.stringify({error: 'not_found'});
                }
                
                targetLabel.scrollIntoView({block: 'center', behavior: 'instant'});
                
                // Try clicking it
                targetLabel.click();
                
                // Sometimes clicking the input inside the label is required
                const input = targetLabel.querySelector('input');
                if (input) {
                    // input.click(); // Don't double click, but dispatch event if needed
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
                
                return JSON.stringify({success: true, text: targetLabel.textContent.trim()});
            }
            """, term)

            data = __import__('json').loads(click_result)
            
            if data.get('success'):
                logger.info(f"[safeway_filter_category] Successfully clicked label for '{term}'. Waiting for SPA to update.")
                # Wait for SPA to update
                await asyncio.sleep(2.5)
                return f"Success: Applied category filter for '{term}'."

    except Exception as e:
        return f"Failure: Error applying filter for '{category_name}'. Sidebar or element may not exist yet or is hidden: {str(e)}"

    return f"Failure: Could not find any category filter matching keywords from '{category_name}'. Consider scrolling the page, opening a collapsed sidebar, or checking if the requested category actually exists on this page."
