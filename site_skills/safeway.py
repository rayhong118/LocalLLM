# site_skills/safeway.py
# Rewritten to use CDP-compatible page.evaluate() instead of Playwright locators.
# browser-use's Page object is a CDP wrapper — it does NOT have .locator(), .get_by_text(), etc.

from browser_use import BrowserSession
import logging
import asyncio
from typing import Any
import config

logger = logging.getLogger(__name__)


async def _llm_match_cards(card_texts: list[str], keyword: str, log_path: str = "") -> list[int]:
    """Use the local LLM to semantically match card texts against a user keyword.
    Returns a list of card indices (from card_texts) that match.
    E.g. keyword='coke' would match a card containing 'Coca-Cola'.
    """
    if not card_texts or not keyword:
        return []

    import httpx
    import json

    # Build a numbered list of card summaries (truncated to save tokens)
    card_list = "\n".join(f"{i}: {text[:100]}" for i, text in enumerate(card_texts))

    system_prompt = (
        "You are a product matching assistant. Given a user's search term and a numbered list of coupon card descriptions, "
        "identify which cards match the search term. Use semantic understanding — for example, 'coke' matches 'Coca-Cola', "
        "'sparkling water' matches 'Topo Chico', 'tissues' matches 'Kleenex' or 'Puffs'.\n"
        "Output ONLY a valid JSON object: {\"matches\": [0, 3, 7]} with the indices of matching cards.\n"
        "If no cards match, output: {\"matches\": []}\n"
        "Be selective — only include cards that are genuinely related to the search term."
    )

    user_msg = f"Search term: \"{keyword}\"\n\nCards:\n{card_list}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": f"{system_prompt}\n\n{user_msg}"}],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0, "num_ctx": 4096}
                },
                timeout=config.LLM_TIMEOUT
            )
            raw = resp.json().get("message", {}).get("content", "{}")
            data = json.loads(raw)
            matches = data.get("matches", [])

            # Validate indices are in range
            valid = [i for i in matches if isinstance(i, int) and 0 <= i < len(card_texts)]

            if log_path:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT LLM MATCH: keyword='{keyword}' -> {len(valid)} matches out of {len(card_texts)} cards: {valid}\n")
            logger.info(f"[_llm_match_cards] keyword='{keyword}' -> {len(valid)} matches: {valid}")
            return valid

    except Exception as e:
        logger.error(f"[_llm_match_cards] LLM matching failed for '{keyword}': {e}. Falling back to empty.")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT LLM MATCH FAILED: {e}\n")
        return []


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
            const seen = new Set();
            
            for (let i = 0; i < cards.length; i++) {
                try {
                    let text = cards[i].innerText.trim().replace(/\\n/g, ' | ').substring(0, 200);
                    if (kwLower && text.toLowerCase().indexOf(kwLower) === -1) continue;
                    
                    if (!seen.has(text)) {
                        seen.add(text);
                        deals.push('Card ' + i + ': ' + text);
                        if (deals.length >= 60) break; // Limit applied AFTER filtering and deduping
                    }
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


async def safeway_clip_coupon(browser: BrowserSession, index: int):
    """SITE-SPECIFIC SKILL: Clips (activates) a coupon on the Safeway deals page by clicking
    the 'Clip Coupon' button on the coupon card at the given index.
    Returns success/failure and the coupon name if available.

    Args:
        index: The index of the coupon card to clip (0 for first, 1 for second, etc.)
    """
    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_clip_coupon] Attempting to clip coupon at card index {index}")

        result_raw = await page.evaluate("""
        (idx) => {
            // Re-use the same card-discovery logic as safeway_get_all_deals
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
                    if (els.length > 0) { cards = els; break; }
                } catch(e) {}
            }

            if (cards.length === 0)
                return JSON.stringify({error: 'No coupon cards found on the page.'});
            if (idx >= cards.length)
                return JSON.stringify({error: 'Index ' + idx + ' out of bounds (' + cards.length + ' cards found)'});

            const card = cards[idx];
            const cardText = (card.innerText || '').substring(0, 120);

            // Check if already clipped
            const alreadyClipped = Array.from(card.querySelectorAll('button, span, div'))
                .some(el => {
                    const t = (el.textContent || '').trim().toLowerCase();
                    return t === 'coupon clipped' || t === 'added' || t === 'added to card' || t === 'clipped';
                });
            if (alreadyClipped) {
                return JSON.stringify({status: 'already_clipped', cardText});
            }

            // Find the clip button within this card
            const clipBtn = Array.from(card.querySelectorAll('button, a, [role="button"]'))
                .find(el => {
                    const t = (el.textContent || '').trim().toLowerCase();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    return (
                        t.includes('clip coupon') || t.includes('clip deal') ||
                        t.includes('load to card') || t.includes('add to card') ||
                        t === 'clip' ||
                        aria.includes('clip coupon') || aria.includes('clip deal') ||
                        aria.includes('load to card') || aria.includes('add to card')
                    ) && el.offsetParent !== null;
                });

            if (!clipBtn) {
                // Maybe it's a "sale price" deal with no clip button
                return JSON.stringify({error: 'no_clip_button', cardText,
                    hint: 'This deal may not require clipping (automatic sale price).'});
            }

            clipBtn.scrollIntoView({block: 'center'});
            clipBtn.click();
            return JSON.stringify({status: 'clicked', cardText, btnText: clipBtn.textContent.trim()});
        }
        """, index)

        import json
        data = json.loads(result_raw)

        if 'error' in data:
            hint = data.get('hint', '')
            card_text = data.get('cardText', '')
            msg = f"Failure: {data['error']}"
            if hint:
                msg += f" ({hint})"
            if card_text:
                msg += f" Card: '{card_text}'"
            logger.warning(f"[safeway_clip_coupon] {msg}")
            return msg

        if data.get('status') == 'already_clipped':
            msg = f"Already clipped: Card {index} ('{data.get('cardText', '')}') is already clipped."
            logger.info(f"[safeway_clip_coupon] {msg}")
            return msg

        # Wait for SPA confirmation animation
        await asyncio.sleep(2.0)

        # Verify the clip took effect
        verify_raw = await page.evaluate("""
        (idx) => {
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
                    if (els.length > 0) { cards = els; break; }
                } catch(e) {}
            }
            if (idx >= cards.length) return JSON.stringify({verified: 'unknown'});
            const card = cards[idx];
            const clipped = Array.from(card.querySelectorAll('button, span, div'))
                .some(el => {
                    const t = (el.textContent || '').trim().toLowerCase();
                    return t === 'coupon clipped' || t === 'added' || t === 'added to card' || t === 'clipped';
                });
            return JSON.stringify({verified: clipped});
        }
        """, index)

        verify = json.loads(verify_raw)
        card_text = data.get('cardText', '')

        if verify.get('verified') is True:
            msg = f"Success: Clipped coupon at card {index} ('{card_text}'). Verified as clipped."
            logger.info(f"[safeway_clip_coupon] {msg}")
            return msg
        else:
            msg = f"Clicked clip button on card {index} ('{card_text}') but could not verify. It may still have worked."
            logger.warning(f"[safeway_clip_coupon] {msg}")
            return msg

    except Exception as e:
        logger.error(f"[safeway_clip_coupon] Error: {e}")
        return f"Failure: Error in safeway_clip_coupon: {str(e)}"


async def safeway_clip_all_matching(browser: BrowserSession, keyword: str = ""):
    """SITE-SPECIFIC SKILL: Finds ALL coupon cards matching a keyword and clips every one
    that has a 'Clip Coupon' button. Returns a summary of how many were clipped.

    Args:
        keyword: Filter keyword to match against coupon card text (case-insensitive).
    """
    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_clip_all_matching] Clipping all coupons matching '{keyword}'")
        await asyncio.sleep(1.5)

        result_raw = await page.evaluate("""
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
                    if (els.length > 0) { cards = els; break; }
                } catch(e) {}
            }

            if (cards.length === 0)
                return JSON.stringify({error: 'No coupon cards found on the page.'});

            const kwLower = (kw || '').toLowerCase();
            const results = [];

            for (let i = 0; i < cards.length; i++) {
                const card = cards[i];
                const cardText = (card.innerText || '').substring(0, 120).replace(/\\n/g, ' | ');

                // Keyword filter
                if (kwLower && cardText.toLowerCase().indexOf(kwLower) === -1) continue;

                // Already clipped?
                const alreadyClipped = Array.from(card.querySelectorAll('button, span, div'))
                    .some(el => {
                        const t = (el.textContent || '').trim().toLowerCase();
                        return t === 'coupon clipped' || t === 'added' || t === 'added to card' || t === 'clipped';
                    });
                if (alreadyClipped) {
                    results.push({index: i, status: 'already_clipped', cardText});
                    continue;
                }

                // Find clip button
                const clipBtn = Array.from(card.querySelectorAll('button, a, [role="button"]'))
                    .find(el => {
                        const t = (el.textContent || '').trim().toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        return (
                            t.includes('clip coupon') || t.includes('clip deal') ||
                            t.includes('load to card') || t.includes('add to card') ||
                            t === 'clip' ||
                            aria.includes('clip coupon') || aria.includes('clip deal') ||
                            aria.includes('load to card') || aria.includes('add to card')
                        ) && el.offsetParent !== null;
                    });

                if (!clipBtn) {
                    results.push({index: i, status: 'no_button', cardText});
                    continue;
                }

                clipBtn.scrollIntoView({block: 'center'});
                clipBtn.click();
                results.push({index: i, status: 'clicked', cardText});
            }

            return JSON.stringify({total: cards.length, results});
        }
        """, keyword)

        import json
        data = json.loads(result_raw)

        if 'error' in data:
            return f"Failure: {data['error']}"

        results = data.get('results', [])
        if not results:
            return f"No coupons matching '{keyword}' found among {data.get('total', 0)} cards."

        clipped = [r for r in results if r['status'] == 'clicked']
        already = [r for r in results if r['status'] == 'already_clipped']
        no_btn = [r for r in results if r['status'] == 'no_button']

        # Wait for SPA to process all the clicks
        if clipped:
            await asyncio.sleep(3.0)

        summary_parts = []
        if clipped:
            summary_parts.append(f"Clipped {len(clipped)} coupon(s)")
        if already:
            summary_parts.append(f"{len(already)} already clipped")
        if no_btn:
            summary_parts.append(f"{len(no_btn)} had no clip button (auto sale)")

        detail_lines = []
        for r in results:
            status_label = {'clicked': '✅ CLIPPED', 'already_clipped': '⏭️ ALREADY', 'no_button': '➖ NO BUTTON'}
            detail_lines.append(f"  Card {r['index']}: {status_label.get(r['status'], r['status'])} - {r.get('cardText', '')}")

        summary = f"Summary: {', '.join(summary_parts)} (out of {data.get('total', 0)} total cards)."
        logger.info(f"[safeway_clip_all_matching] {summary}")
        return summary + "\n" + "\n".join(detail_lines)

    except Exception as e:
        logger.error(f"[safeway_clip_all_matching] Error: {e}")
        return f"Failure: Error in safeway_clip_all_matching: {str(e)}"

async def safeway_clip_by_indices(browser: BrowserSession, indices: list[int], keyword: str = ""):
    """Clips coupons at specific card indices. Used with _llm_match_cards for semantic matching.
    
    Args:
        indices: List of card indices to clip.
        keyword: The original keyword (for logging only).
    """
    if not indices:
        return f"No matching cards to clip for '{keyword}'."

    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_clip_by_indices] Clipping {len(indices)} cards for '{keyword}': {indices}")
        await asyncio.sleep(1.0)

        result_raw = await page.evaluate("""
        (targetIndices) => {
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
                    if (els.length > 0) { cards = els; break; }
                } catch(e) {}
            }

            if (cards.length === 0)
                return JSON.stringify({error: 'No coupon cards found on the page.'});

            const results = [];

            for (const idx of targetIndices) {
                if (idx >= cards.length) {
                    results.push({index: idx, status: 'out_of_bounds'});
                    continue;
                }

                const card = cards[idx];
                const cardText = (card.innerText || '').substring(0, 120).replace(/\\n/g, ' | ');

                // Already clipped?
                const alreadyClipped = Array.from(card.querySelectorAll('button, span, div'))
                    .some(el => {
                        const t = (el.textContent || '').trim().toLowerCase();
                        return t === 'coupon clipped' || t === 'added' || t === 'added to card' || t === 'clipped';
                    });
                if (alreadyClipped) {
                    results.push({index: idx, status: 'already_clipped', cardText});
                    continue;
                }

                // Find clip button
                const clipBtn = Array.from(card.querySelectorAll('button, a, [role="button"]'))
                    .find(el => {
                        const t = (el.textContent || '').trim().toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        return (
                            t.includes('clip coupon') || t.includes('clip deal') ||
                            t.includes('load to card') || t.includes('add to card') ||
                            t === 'clip' ||
                            aria.includes('clip coupon') || aria.includes('clip deal') ||
                            aria.includes('load to card') || aria.includes('add to card')
                        ) && el.offsetParent !== null;
                    });

                if (!clipBtn) {
                    results.push({index: idx, status: 'no_button', cardText});
                    continue;
                }

                clipBtn.scrollIntoView({block: 'center'});
                clipBtn.click();
                results.push({index: idx, status: 'clicked', cardText});
            }

            return JSON.stringify({total: cards.length, results});
        }
        """, indices)

        import json
        data = json.loads(result_raw)

        if 'error' in data:
            return f"Failure: {data['error']}"

        results = data.get('results', [])
        if not results:
            return f"No cards to clip for '{keyword}'."

        clipped = [r for r in results if r['status'] == 'clicked']
        already = [r for r in results if r['status'] == 'already_clipped']
        no_btn = [r for r in results if r['status'] == 'no_button']

        if clipped:
            await asyncio.sleep(3.0)

        summary_parts = []
        if clipped:
            summary_parts.append(f"Clipped {len(clipped)} coupon(s)")
        if already:
            summary_parts.append(f"{len(already)} already clipped")
        if no_btn:
            summary_parts.append(f"{len(no_btn)} had no clip button (auto sale)")

        detail_lines = []
        for r in results:
            status_label = {'clicked': '✅ CLIPPED', 'already_clipped': '⏭️ ALREADY', 'no_button': '➖ NO BUTTON', 'out_of_bounds': '⚠️ INVALID INDEX'}
            detail_lines.append(f"  Card {r['index']}: {status_label.get(r['status'], r['status'])} - {r.get('cardText', '')}")

        summary = f"Summary: {', '.join(summary_parts)} (for '{keyword}')."
        logger.info(f"[safeway_clip_by_indices] {summary}")
        return summary + "\n" + "\n".join(detail_lines)

    except Exception as e:
        logger.error(f"[safeway_clip_by_indices] Error: {e}")
        return f"Failure: Error in safeway_clip_by_indices: {str(e)}"


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
                const targetSelectors = 'label, [role="checkbox"], button, a, [data-qa*="category"], [data-testid*="category"]';
                
                for (const container of filterContainers) {
                    const labels = container.querySelectorAll(targetSelectors);
                    for (const label of labels) {
                        if ((label.textContent || '').toLowerCase().includes(targetLower) && label.offsetParent !== null) {
                            targetLabel = label;
                            break;
                        }
                    }
                    if (targetLabel) break;
                }
                
                if (!targetLabel) {
                    const mainArea = document.querySelector('main, [role="main"]') || document.body;
                    const allLabels = Array.from(mainArea.querySelectorAll(targetSelectors))
                        .filter(el => !el.closest('header, [class*="global-header"], [id*="header"]'));
                        
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

async def safeway_get_categories(browser: BrowserSession):
    """SITE-SPECIFIC SKILL: Scrapes the list of all available category filters 
    from the Safeway sidebar. Returns a list of category names.
    """
    page = await browser.get_current_page()
    try:
        logger.info("[safeway_get_categories] Waiting for sidebar filters to load...")
        # Wait for any potential filter container to appear
        try:
            await page.wait_for_selector('[class*="filter"], [class*="sidebar"]', timeout=10000)
            await asyncio.sleep(3) # Wait for SPA to populate the empty container
        except Exception:
            await asyncio.sleep(2) # Wait anyway just in case the skeleton selector failed
            
        categories_raw = await page.evaluate("""
        () => {
            // Attempt to expand a "Filter" or "Categories" drawer if it exists and is closed
            const openBtns = Array.from(document.querySelectorAll('button')).filter(b => {
                const txt = (b.textContent || '').toLowerCase();
                return (txt.includes('filter') || txt.includes('categor')) && b.offsetParent !== null;
            });
            if (openBtns.length > 0) {
                try { openBtns[0].click(); } catch(e) {}
            }

            const selectors = [
                '[class*="filter"] label', '[class*="sidebar"] label',
                '[id*="filter"] label', '[role="checkbox"]',
                '[class*="filter-label"]', '[class*="category-name"]',
                'button[class*="filter"]', 'a[class*="category"]',
                '[data-qa*="category"]', '[data-testid*="category"]'
            ];
            const labels = [];
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach(el => {
                    // Get text and clean it up
                    let text = (el.innerText || el.textContent || '').trim();
                    // Some labels have counts like "Produce (12)", strip them
                    text = text.replace(/\\s*\\(\\d+\\)$/, '');
                    
                    // Relaxed visibility check: even if offsetParent is null, Safeway SPA sometimes 
                    // hides filters in a display:none div that can still be toggled.
                    if (text && text.length > 1 && text.length < 40) {
                        if (!labels.includes(text) && !/clear|all|apply|filter|sort/i.test(text)) {
                            labels.push(text);
                        }
                    }
                });
            }
            // Fallback: if we still have 0, look for common grocery terms in main content only
            if (labels.length === 0) {
                const commonCats = ['Produce', 'Meat', 'Seafood', 'Dairy', 'Frozen', 'Beverages', 'Bakery', 'Pantry', 'Snacks'];
                const mainArea = document.querySelector('main, [role="main"], #main-content') || document.body;
                
                const candidates = Array.from(mainArea.querySelectorAll('a, button, span, label, [role="checkbox"]'))
                    .filter(el => !el.closest('header, [class*="global-header"], [id*="header"]'));
                    
                candidates.forEach(el => {
                    const txt = (el.textContent || '').trim().replace(/\\s*\\(\\d+\\)$/, '');
                    if (commonCats.some(c => txt.toLowerCase().includes(c.toLowerCase())) && !labels.includes(txt)) {
                        labels.push(txt);
                    }
                });
            }
            return JSON.stringify(labels);
        }
        """)
        categories = __import__('json').loads(categories_raw)
        logger.info(f"[safeway_get_categories] Found {len(categories)} categories: {categories}")
        return categories
    except Exception as e:
        logger.error(f"[safeway_get_categories] Failed: {e}")
        return []
async def safeway_run_pre_flight(browser: BrowserSession, prompt: str, context_str: str, log_path: str, llm: Any):
    """SITE-SPECIFIC AUTOMATION: Handles the heavy lifting of navigation and 
    scraping for Safeway before the agent starts.
    """
    import asyncio as _asyncio
    import re
    
    try:
        # 1. Navigate to target URL from context
        target_url = "https://www.safeway.com/loyalty/coupons-deals" # Default
        if context_str:
            url_match = re.search(r'https?://[^\s)]+', context_str)
            if url_match:
                target_url = url_match.group(0).rstrip('.')
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- PRE-FLIGHT (Safeway): Navigating to {target_url} ---\n")
            
        page = await browser.get_current_page()
        await page.goto(target_url)
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await _asyncio.sleep(5)

        # 2. Extract keywords using LLM
        extraction_system = (
            "You are a product extraction assistant. Your job is to extract a list of specific products or items from a user's prompt.\n"
            "Output ONLY a valid JSON object with the key 'items' containing a list of strings.\n"
            "Example prompt: 'Get deals for milk, ice cream, and steak'\n"
            "Output: {\"items\": [\"milk\", \"ice cream\", \"steak\"]}\n"
            "If no specific items are found, output {\"items\": []}."
        )
        items = []
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                extraction_resp = await client.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": config.LLM_MODEL,
                        "messages": [{"role": "user", "content": f"{extraction_system}\n\nPrompt: {prompt}"}],
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0, "num_ctx": 4096}
                    },
                    timeout=config.LLM_TIMEOUT
                )
                raw_content = extraction_resp.json().get("message", {}).get("content", "{}")
                import json
                data = json.loads(raw_content)
                items = data.get("items", [])
                logger.info(f"[safeway_run_pre_flight] Extracted items from prompt: {items}")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Extracted items from prompt: {items}\n")
        except Exception as e:
            logger.error(f"[safeway_run_pre_flight] Keyword extraction failed: {e}. Falling back to simple extraction.")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT: Keyword extraction failed: {e}. Falling back to simple extraction.\n")
            noise_words = {"look", "for", "deals", "safeway", "website", "following", "item:", "items:", "item", "items", "search", "find", "on", "products", "product", "the", "a", "an"}
            items = [" ".join([w for w in prompt.lower().split() if w not in noise_words and len(w) > 2]).strip()]

        if not items:
            items = [prompt[:30]]
            
        logger.info(f"[safeway_run_pre_flight] Final list of items to process: {items}")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT: Final list of items to process: {items}\n")

        # 3. Categorize items
        available_categories = await safeway_get_categories(browser)
        item_category_map = {} # category -> list of items
        
        if available_categories and items:
            selector_system = (
                "You are a categorization assistant for a grocery store. You must output ONLY a valid JSON object.\n"
                "Format: {\"mapping\": [{\"item\": \"item name\", \"category\": \"Category Name\"}, ...]}\n"
                "RULES: 1. NEVER pick 'Special Offers' if a specific food category is available. 2. If nothing fits, use 'NONE' as category.\n"
                f"Available Categories: {', '.join(available_categories)}"
            )
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    selector_resp = await client.post(
                        "http://localhost:11434/api/chat",
                        json={
                            "model": config.LLM_MODEL,
                            "messages": [{"role": "user", "content": f"{selector_system}\n\nItems: {', '.join(items)}"}],
                            "stream": False,
                            "format": "json",
                            "options": {"temperature": 0, "num_ctx": 4096}
                        },
                        timeout=config.LLM_TIMEOUT
                    )
                    raw_content = selector_resp.json().get("message", {}).get("content", "{}")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: Categorization response: {raw_content}\n")
                    
                    import json
                    data = json.loads(raw_content)
                    mappings = data.get("mapping", [])
                    
                    logger.info(f"[safeway_run_pre_flight] Item Categorization Breakdown:")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write("PRE-FLIGHT: Item Categorization Breakdown:\n")
                    
                    for m in mappings:
                        item_name = m.get("item")
                        category_choice = m.get("category", "NONE").strip()
                        
                        logger.info(f"  - Item: '{item_name}' -> Identified Category: '{category_choice}'")
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"  - Item: '{item_name}' -> Identified Category: '{category_choice}'\n")
                        
                        if category_choice and category_choice != "NONE":
                            matched_cat = ""
                            if any(c.lower() == category_choice.lower() for c in available_categories):
                                matched_cat = next(c for c in available_categories if c.lower() == category_choice.lower())
                            else:
                                for c in available_categories:
                                    if c.lower() in category_choice.lower() or category_choice.lower() in c.lower():
                                        matched_cat = c
                                        break
                            
                            if matched_cat:
                                if matched_cat not in item_category_map:
                                    item_category_map[matched_cat] = []
                                item_category_map[matched_cat].append(item_name)
            except Exception as e:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Multi-Category mapping failed: {e}\n")

        # Fallback: if mapping failed, put all items in a single list under empty category
        if not item_category_map:
            item_category_map[""] = items

        # 4. Scrape deals AND clip coupons per category
        all_deals = []
        clip_summary = []
        for category, cat_items in item_category_map.items():
            if category:
                with open(log_path, "a", encoding="utf-8") as f: 
                    f.write(f"PRE-FLIGHT: Applying category filter '{category}' for items: {cat_items}...\n")
                await safeway_filter_category(category, browser)
                await _asyncio.sleep(3)
            
            # Scrape for each item in this category (or once if category is empty)
            keywords_to_search = cat_items if category else [" ".join(items)]
            for kw in keywords_to_search:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Scraping deals for '{kw}' in '{category or 'General'}'...\n")
                scrape_result = await safeway_get_all_deals(browser, keyword=kw)
                
                found_deals = False
                if scrape_result.startswith("Found deals:"):
                    all_deals.append(f"--- Deals for '{kw}' in '{category or 'General'}' ---\n" + scrape_result)
                    found_deals = True
                else:
                    # Fallback scrape if no specific match
                    scrape_result = await safeway_get_all_deals(browser, keyword="")
                    if scrape_result.startswith("Found deals:"):
                        all_deals.append(f"--- General Deals in '{category or 'General'}' (Search for '{kw}' failed) ---\n" + scrape_result)
                        found_deals = True

                # 5. Auto-clip matching coupons using LLM-based semantic matching
                if found_deals:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: LLM-matching coupons for '{kw}' in '{category or 'General'}'...\n")
                    
                    # Scrape all card texts from the page for LLM matching
                    card_texts_raw = await page.evaluate("""
                    () => {
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
                                if (els.length > 0) { cards = els; break; }
                            } catch(e) {}
                        }
                        return JSON.stringify(cards.map(c => (c.innerText || '').substring(0, 120).replace(/\\n/g, ' | ')));
                    }
                    """)
                    import json as _json
                    card_texts = _json.loads(card_texts_raw)
                    
                    # Ask LLM which cards match the keyword semantically
                    matching_indices = await _llm_match_cards(card_texts, kw, log_path)
                    
                    if matching_indices:
                        clip_result = await safeway_clip_by_indices(browser, matching_indices, keyword=kw)
                    else:
                        clip_result = f"LLM found no semantic matches for '{kw}' among {len(card_texts)} cards."
                    
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT CLIP RESULT: {clip_result}\n")
                    logger.info(f"[safeway_run_pre_flight] Clip result for '{kw}': {clip_result.splitlines()[0] if clip_result else 'empty'}")
                    clip_summary.append(f"--- Clip results for '{kw}' ---\n{clip_result}")

        if not all_deals:
            return "No deals found for the requested items."
        
        result_parts = all_deals
        if clip_summary:
            result_parts.append("\n=== COUPON CLIPPING RESULTS ===")
            result_parts.extend(clip_summary)
            
        return "\n\n".join(result_parts)

    except Exception as e:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT ERROR: {e}\n")
        return ""
