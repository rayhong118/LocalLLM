# site_skills/safeway.py
# Rewritten to use CDP-compatible page.evaluate() instead of Playwright locators.
# browser-use's Page object is a CDP wrapper — it does NOT have .locator(), .get_by_text(), etc.

from browser_use import BrowserSession
import logging
import asyncio
from typing import Any
from backend import config
from backend.services.llm import clean_json_text
import re
import json
import httpx

logger = logging.getLogger(__name__)

# --- Global JS Snippets & Selectors for DRY code ---
CARD_SELECTORS_JS = """
const selectors = [
    '[class*="coupon-card__card-body"]'
];
"""

ALREADY_CLIPPED_JS = """
const alreadyClipped = Array.from(card.querySelectorAll('button, span, div'))
    .some(el => {
        const t = (el.textContent || '').trim().toLowerCase();
        return t === 'coupon clipped' || t === 'added' || t === 'added to card' || t === 'clipped';
    });
"""

VERIFY_CLIPPED_JS = """
const clipped = Array.from(card.querySelectorAll('button, span, div'))
    .some(el => {
        const t = (el.textContent || '').trim().toLowerCase();
        return t === 'coupon clipped' || t === 'added' || t === 'added to card' || t === 'clipped';
    });
"""

def _log_to_file(log_path: str, msg: str):
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

def _clean_deal_description(desc: str) -> str:
    """Strip Safeway UI noise (button labels, duplicate expiry formats) from card descriptions."""
    # Remove common UI button labels
    noise_patterns = [
        r'\|\s*Offer Details?\s*',
        r'\|\s*Clip Coupon\s*',
        r'\|\s*Clip Deal\s*',
        r'\|\s*Coupon has already been\s*',
        r'\|\s*Clipped\s*',
        r'\|\s*Add to Card\s*',
        r'\|\s*Load to Card\s*',
    ]
    for pattern in noise_patterns:
        desc = re.sub(pattern, ' | ', desc, flags=re.IGNORECASE)
    
    # Remove duplicate expiration (keep short form, drop long form)
    # e.g. "Expires 5/5/2026 | Expires May 5, 2026" -> "Expires 5/5/2026"
    desc = re.sub(r'\|\s*Expires\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s*\d{4}\s*', '', desc)
    
    # Clean up multiple pipes and trailing pipes
    desc = re.sub(r'(\s*\|\s*){2,}', ' | ', desc)
    desc = desc.strip().strip('|').strip()
    
    return desc

def _extract_query_tokens(keyword: str) -> list[str]:
    """Generic token extraction for any search term (no hardcoded items)."""
    kw_clean = keyword.lower().strip()
    stop_words = {"and", "or", "the", "in", "with", "for", "of", "a", "an", "to"}
    words = [w for w in re.findall(r'[a-zA-Z0-9]+', kw_clean) if len(w) >= 3 and w not in stop_words]
    tokens = [kw_clean] if kw_clean else []
    for w in words:
        if w not in tokens:
            tokens.append(w)
    return tokens


async def _llm_match_cards_for_category(
    card_texts: list[str], 
    target_items: list[str], 
    category: str = "", 
    log_path: str = ""
) -> dict[str, list[int]] | None:
    """Use the local LLM to semantically match card product titles against all target items
    in a selected category in a single pass.
    Batches cards in compact chunks (10 cards) with max_tokens: 800 to ensure thinking
    models finish reasoning and output clean JSON without token exhaustion.
    Returns: {item_name: [matching_card_indices], ...} or None if all calls fail.
    """
    if not card_texts or not target_items:
        return {item: [] for item in target_items}

    chunk_size = 10
    total_cards = len(card_texts)
    chunks = [
        list(range(i, min(i + chunk_size, total_cards)))
        for i in range(0, total_cards, chunk_size)
    ]

    _log_to_file(
        log_path,
        f"PRE-FLIGHT CATEGORY MATCHING: Evaluating {total_cards} cards in {len(chunks)} chunk(s) (batch size {chunk_size}) for {len(target_items)} target item(s) in '{category or 'General'}': {target_items}"
    )
    logger.info(f"[_llm_match_cards_for_category] Evaluating {total_cards} cards across {len(chunks)} chunks for {target_items} in '{category}'")

    system_prompt = (
        "You are a strict product matching assistant. Given a list of target search items and a numbered list of product titles, "
        "determine which cards match each target item.\n\n"
        "STRICT RULES:\n"
        "1. Match the target item or a specific brand/variety of it (e.g. 'ice cream' matches 'Häagen-Dazs Novelties', 'gelato', 'ice cream bars'; 'coke' matches 'Coca-Cola'; 'paper tissues' matches 'Kleenex Facial Tissues').\n"
        "2. If a card is a DIFFERENT product type, do NOT match it even if in the same category (e.g. pizza rolls or waffles are NOT ice cream; plain spring water is NOT sparkling water).\n"
        "3. Output ONLY a valid JSON object mapping each target item to an array of matching card indices. If no cards match, use an empty array.\n"
        "Example output format:\n"
        "{\"matches\": {\"coke\": [2], \"sparkling water\": [5], \"Arizona tea\": []}}\n"
        "Be direct and do NOT write lengthy explanations or reasoning.\n"
    )

    all_matches_by_item = {item: [] for item in target_items}
    had_successful_call = False

    async with httpx.AsyncClient() as client:
        for chunk_idx, indices in enumerate(chunks):
            card_list = "\n".join(f"{i}: {card_texts[i][:100]}" for i in indices)
            user_msg = f"Target items: {json.dumps(target_items)}\n\nCards:\n{card_list}"

            try:
                resp = await client.post(
                    f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                    json={
                        "model": config.LLM_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg}
                        ],
                        "stream": False,
                        "temperature": 0,
                        "max_tokens": 800
                    },
                    timeout=min(config.LLM_TIMEOUT, 90)
                )

                if resp.status_code != 200:
                    err_msg = f"LM Studio returned HTTP {resp.status_code} on chunk {chunk_idx + 1}/{len(chunks)}: {resp.text[:200]}"
                    logger.error(f"[_llm_match_cards_for_category] {err_msg}")
                    _log_to_file(log_path, f"PRE-FLIGHT LLM CHUNK ERROR: {err_msg}")
                    continue

                resp_json = resp.json()
                raw = ""
                if "choices" in resp_json and len(resp_json["choices"]) > 0:
                    choice = resp_json["choices"][0]
                    msg = choice.get("message", {})
                    raw = msg.get("content", "")
                    if (not raw or not raw.strip()) and msg.get("reasoning_content"):
                        reasoning = msg.get("reasoning_content", "")
                        if "{" in reasoning and "}" in reasoning:
                            raw = reasoning
                elif "message" in resp_json:
                    raw = resp_json.get("message", {}).get("content", "")

                if not raw or not raw.strip():
                    empty_msg = f"LM Studio returned empty content on chunk {chunk_idx + 1}/{len(chunks)}"
                    logger.warning(f"[_llm_match_cards_for_category] {empty_msg}")
                    _log_to_file(log_path, f"PRE-FLIGHT LLM CHUNK WARNING: {empty_msg}")
                    continue

                try:
                    data = json.loads(clean_json_text(raw))
                    had_successful_call = True
                    matches_obj = data.get("matches", data)
                    if isinstance(matches_obj, dict):
                        for item in target_items:
                            item_indices = []
                            for k, v in matches_obj.items():
                                if k.lower().strip() == item.lower().strip() and isinstance(v, list):
                                    item_indices = [i for i in v if isinstance(i, int) and i in indices]
                                    break
                            all_matches_by_item[item].extend(item_indices)
                except json.JSONDecodeError as je:
                    parse_msg = f"Failed to parse JSON on chunk {chunk_idx + 1}/{len(chunks)}: {je}. Raw snippet: {raw[:150]}"
                    logger.warning(f"[_llm_match_cards_for_category] {parse_msg}")
                    _log_to_file(log_path, f"PRE-FLIGHT LLM CHUNK WARNING: {parse_msg}")
                    continue

            except httpx.TimeoutException:
                timeout_msg = f"Chunk {chunk_idx + 1}/{len(chunks)} timed out after {min(config.LLM_TIMEOUT, 90)}s in '{category}'"
                logger.error(f"[_llm_match_cards_for_category] {timeout_msg}")
                _log_to_file(log_path, f"PRE-FLIGHT LLM CHUNK TIMEOUT: {timeout_msg}")
                continue
            except Exception as e:
                ex_msg = f"Error on chunk {chunk_idx + 1}/{len(chunks)} in '{category}': {type(e).__name__}: {e}"
                logger.error(f"[_llm_match_cards_for_category] {ex_msg}")
                _log_to_file(log_path, f"PRE-FLIGHT LLM CHUNK ERROR: {ex_msg}")
                continue

    if not had_successful_call and chunks:
        _log_to_file(log_path, f"PRE-FLIGHT LLM MATCH FAILED: All {len(chunks)} chunk calls failed in '{category}' for {target_items}")
        return None

    for item in target_items:
        all_matches_by_item[item] = list(dict.fromkeys(all_matches_by_item[item]))
        _log_to_file(log_path, f"PRE-FLIGHT LLM MATCH: item='{item}' -> {len(all_matches_by_item[item])} matches in '{category}': {all_matches_by_item[item]}")
        logger.info(f"[_llm_match_cards_for_category] item='{item}' -> {len(all_matches_by_item[item])} matches: {all_matches_by_item[item]}")

    return all_matches_by_item



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

        terms = _extract_query_tokens(keyword) if keyword else []

        # Scrape all card-like elements using JS
        js_code = """
        (terms) => {
            INSERT_SELECTORS_HERE
            
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
            
            const deals = [];
            const seen = new Set();
            
            for (let i = 0; i < cards.length; i++) {
                try {
                    let text = cards[i].innerText.trim().replace(/\\n/g, ' | ').substring(0, 300);
                    const tLower = text.toLowerCase();
                    if (terms && terms.length > 0) {
                        const matched = terms.some(t => tLower.indexOf(t) !== -1);
                        if (!matched) continue;
                    }
                    
                    if (!seen.has(text)) {
                        seen.add(text);
                        deals.push('Card ' + i + ': ' + text);
                        if (deals.length >= 60) break; // Limit applied AFTER filtering and deduping
                    }
                } catch(e) {}
            }
            
            return JSON.stringify({total: cards.length, deals: deals});
        }
        """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS)

        raw = await page.evaluate(js_code, terms)
        data = json.loads(raw)
        
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

        # Probe the card: check status, find clip button, return coordinates for real click
        probe_js = """
        (idx) => {
            INSERT_SELECTORS_HERE

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
            const cardText = (card.innerText || '').substring(0, 250).replace(/\\n/g, ' | ');

            // Check if already clipped
            INSERT_ALREADY_CLIPPED_HERE
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
                return JSON.stringify({error: 'no_clip_button', cardText,
                    hint: 'This deal may not require clipping (automatic sale price).'});
            }

            // Scroll button into view and return its coordinates for a real Playwright click
            clipBtn.scrollIntoView({block: 'center'});
            const rect = clipBtn.getBoundingClientRect();
            return JSON.stringify({
                status: 'ready_to_click',
                cardText,
                btnText: clipBtn.textContent.trim(),
                x: Math.round(rect.x + rect.width / 2),
                y: Math.round(rect.y + rect.height / 2)
            });
        }
        """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS).replace("INSERT_ALREADY_CLIPPED_HERE", ALREADY_CLIPPED_JS)

        result_raw = await page.evaluate(probe_js, index)
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

        # Use Playwright's real mouse click instead of JS element.click()
        click_x = data['x']
        click_y = data['y']
        try:
            mouse = await page.mouse
            await mouse.click(click_x, click_y)
            logger.info(f"[safeway_clip_coupon] Real mouse click at ({click_x}, {click_y}) for card {index}")
        except Exception as click_err:
            logger.warning(f"[safeway_clip_coupon] mouse.click failed: {click_err}, falling back to JS click")
            fallback_js = """
            (idx) => {
                INSERT_SELECTORS_HERE
                let cards = [];
                for (const sel of selectors) {
                    try {
                        const els = Array.from(document.querySelectorAll(sel))
                            .filter(el => el.offsetParent !== null);
                        if (els.length > 0) { cards = els; break; }
                    } catch(e) {}
                }
                if (idx >= cards.length) return;
                const card = cards[idx];
                const clipBtn = Array.from(card.querySelectorAll('button, a, [role="button"]'))
                    .find(el => {
                        const t = (el.textContent || '').trim().toLowerCase();
                        return t.includes('clip coupon') || t.includes('clip deal') || t === 'clip';
                    });
                if (clipBtn) clipBtn.click();
            }
            """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS)
            await page.evaluate(fallback_js, index)

        # Wait for SPA confirmation animation
        await asyncio.sleep(2.0)

        # Verify the clip took effect
        verify_js = """
        (idx) => {
            INSERT_SELECTORS_HERE
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
            INSERT_VERIFY_CLIPPED_HERE
            return JSON.stringify({verified: clipped});
        }
        """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS).replace("INSERT_VERIFY_CLIPPED_HERE", VERIFY_CLIPPED_JS)

        verify_raw = await page.evaluate(verify_js, index)
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

        # Evaluate JS to find all matching card indices on the page
        js_code = """
        (kw) => {
            INSERT_SELECTORS_HERE

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
            const indices = [];

            for (let i = 0; i < cards.length; i++) {
                const card = cards[i];
                const cardText = (card.innerText || '').substring(0, 250).replace(/\\n/g, ' | ');

                // Keyword filter
                if (kwLower && cardText.toLowerCase().indexOf(kwLower) === -1) continue;

                // Add index to list of matching cards
                indices.push(i);
            }

            return JSON.stringify({total: cards.length, indices: indices});
        }
        """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS)

        matching_indices_raw = await page.evaluate(js_code, keyword)
        data = json.loads(matching_indices_raw)

        if 'error' in data:
            return f"Failure: {data['error']}"

        indices = data.get('indices', [])
        if not indices:
            return f"No coupons matching '{keyword}' found among {data.get('total', 0)} cards."

        # Delegate the actual clipping and sequential wait/verification to safeway_clip_by_indices
        summary_str, _ = await safeway_clip_by_indices(browser, indices, keyword=keyword)
        return summary_str

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
        return f"No matching cards to clip for '{keyword}'.", []

    # Deduplicate indices preserving order
    indices = list(dict.fromkeys(indices))

    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_clip_by_indices] Clipping {len(indices)} cards for '{keyword}': {indices}")
        await asyncio.sleep(1.0)

        results = []

        for idx in indices:
            # Probe this card: check status, find clip button, return coordinates for real click
            probe_js = """
            (idx) => {
                INSERT_SELECTORS_HERE

                let cards = [];
                for (const sel of selectors) {
                    try {
                        const els = Array.from(document.querySelectorAll(sel))
                            .filter(el => el.offsetParent !== null);
                        if (els.length > 0) { cards = els; break; }
                    } catch(e) {}
                }

                if (cards.length === 0)
                    return JSON.stringify({error: 'No coupon cards found on the page.', total: 0});
                if (idx >= cards.length)
                    return JSON.stringify({status: 'out_of_bounds', total: cards.length});

                const card = cards[idx];
                const cardText = (card.innerText || '').substring(0, 250).replace(/\\n/g, ' | ');

                // Check if already clipped
                INSERT_ALREADY_CLIPPED_HERE
                if (alreadyClipped) {
                    return JSON.stringify({status: 'already_clipped', cardText, total: cards.length});
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
                    return JSON.stringify({status: 'no_button', cardText, total: cards.length});
                }

                // Scroll button into view and return its coordinates for a real Playwright click
                clipBtn.scrollIntoView({block: 'center'});
                const rect = clipBtn.getBoundingClientRect();
                return JSON.stringify({
                    status: 'ready_to_click',
                    cardText,
                    total: cards.length,
                    x: Math.round(rect.x + rect.width / 2),
                    y: Math.round(rect.y + rect.height / 2)
                });
            }
            """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS).replace("INSERT_ALREADY_CLIPPED_HERE", ALREADY_CLIPPED_JS)

            probe_raw = await page.evaluate(probe_js, idx)
            probe = json.loads(probe_raw)

            if 'error' in probe:
                return f"Failure: {probe['error']}", []

            status = probe.get('status')
            card_text = probe.get('cardText', '')

            if status == 'ready_to_click':
                # Use Playwright's real mouse click
                click_x = probe['x']
                click_y = probe['y']
                try:
                    mouse = await page.mouse
                    await mouse.click(click_x, click_y)
                    logger.info(f"[safeway_clip_by_indices] Real mouse click at ({click_x}, {click_y}) for card {idx}")
                except Exception as click_err:
                    logger.warning(f"[safeway_clip_by_indices] mouse.click failed for card {idx}: {click_err}, falling back to JS click")
                    fallback_js = """
                    (idx) => {
                        INSERT_SELECTORS_HERE
                        let cards = [];
                        for (const sel of selectors) {
                            try {
                                const els = Array.from(document.querySelectorAll(sel))
                                    .filter(el => el.offsetParent !== null);
                                if (els.length > 0) { cards = els; break; }
                            } catch(e) {}
                        }
                        if (idx >= cards.length) return;
                        const card = cards[idx];
                        const clipBtn = Array.from(card.querySelectorAll('button, a, [role="button"]'))
                            .find(el => {
                                const t = (el.textContent || '').trim().toLowerCase();
                                return t.includes('clip coupon') || t.includes('clip deal') || t === 'clip';
                            });
                        if (clipBtn) clipBtn.click();
                    }
                    """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS)
                    await page.evaluate(fallback_js, idx)

                # Wait for SPA to process the click
                await asyncio.sleep(1.5)

                # Verify clipping
                verify_js = """
                (idx) => {
                    INSERT_SELECTORS_HERE
                    let cards = [];
                    for (const sel of selectors) {
                        try {
                            const els = Array.from(document.querySelectorAll(sel))
                                .filter(el => el.offsetParent !== null);
                            if (els.length > 0) { cards = els; break; }
                        } catch(e) {}
                    }
                    if (idx >= cards.length) return 'false';
                    const card = cards[idx];
                    INSERT_VERIFY_CLIPPED_HERE
                    return clipped ? 'true' : 'false';
                }
                """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS).replace("INSERT_VERIFY_CLIPPED_HERE", VERIFY_CLIPPED_JS)

                verify_raw = await page.evaluate(verify_js, idx)
                is_verified = (verify_raw == 'true' or verify_raw is True)

                if is_verified:
                    results.append({'index': idx, 'status': 'clicked', 'cardText': card_text})
                    logger.info(f"[safeway_clip_by_indices] ✅ Clipped and VERIFIED card {idx}")
                else:
                    # Retry verification once with an extra wait
                    await asyncio.sleep(2.0)
                    verify_raw2 = await page.evaluate(verify_js, idx)
                    is_verified2 = (verify_raw2 == 'true' or verify_raw2 is True)
                    if is_verified2:
                        results.append({'index': idx, 'status': 'clicked', 'cardText': card_text})
                        logger.info(f"[safeway_clip_by_indices] ✅ Clipped and VERIFIED card {idx} on retry")
                    else:
                        results.append({'index': idx, 'status': 'clicked', 'cardText': card_text})
                        logger.warning(f"[safeway_clip_by_indices] ⚠️ Clicked card {idx} but could not verify. May still have worked.")
            else:
                results.append({'index': idx, 'status': status, 'cardText': card_text})

        clipped = [r for r in results if r['status'] == 'clicked']
        already = [r for r in results if r['status'] == 'already_clipped']
        no_btn = [r for r in results if r['status'] == 'no_button']

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
        return summary + "\n" + "\n".join(detail_lines), results

    except Exception as e:
        logger.error(f"[safeway_clip_by_indices] Error: {e}")
        return f"Failure: Error in safeway_clip_by_indices: {str(e)}", []


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
                
                // Click once cleanly without double event dispatching
                targetLabel.click();
                
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
        # Retry loop: Safeway's SPA sometimes takes several seconds after networkidle to render the sidebar
        categories = []
        for attempt in range(5):
            try:
                await page.wait_for_selector('[class*="filter"], [class*="sidebar"]', timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(3)

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
            logger.info(f"[safeway_get_categories] Attempt {attempt+1}: Found {len(categories)} categories")
            if categories:
                break  # Found categories, no need to retry

        logger.info(f"[safeway_get_categories] Found {len(categories)} categories: {categories}")
        return categories
    except Exception as e:
        logger.error(f"[safeway_get_categories] Failed: {e}")
        return []

CATEGORY_EMOJIS = {
    "Beverages": "🥤",
    "Dairy, Eggs & Cheese": "🥛",
    "Dairy": "🥛",
    "Meat & Seafood": "🥩",
    "Fruits & Vegetables": "🍎",
    "Produce": "🍎",
    "Frozen Foods": "❄️",
    "Paper, Cleaning & Home": "🧻",
    "Paper": "🧻",
    "Baby Care": "👶",
    "Bread & Bakery": "🍞",
    "Breakfast & Cereal": "🥣",
    "Cookies, Snacks & Candy": "🍪",
    "Deli": "🧀",
    "Personal Care & Health": "🧼",
    "Pet Care": "🐾",
}

def _format_deal_markdown(desc: str) -> str:
    parts = [p.strip() for p in desc.split('|') if p.strip()]
    if not parts:
        return desc
    
    deal = parts[0]
    product = parts[1] if len(parts) > 1 else ""
    details = parts[2] if len(parts) > 2 else ""
    
    terms = []
    for p in parts[3:]:
        if re.search(r'offer details|clip coupon|clip deal|add to card|load to card', p, re.IGNORECASE):
            continue
        terms.append(p)
        
    formatted = f"**{deal}**"
    if product:
        formatted += f" - *{product}*"
    
    if details:
        formatted += f"\n     * *Details:* {details}"
    if terms:
        formatted += f"\n     * *Terms:* {' | '.join(terms)}"
        
    return formatted

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
        await _asyncio.sleep(8)  # Safeway SPA needs extra time after networkidle to render coupon cards

        # 1.5 Check page state and sign-in status
        # browser-use CDP wrapper returns JSON strings, not raw objects — must use JSON.stringify
        session_signed_in = True  # default; used below to gate scraping
        try:
            page_state_raw = await page.evaluate("""
            () => {
                const url = window.location.href;
                const bodyText = (document.body.innerText || '').trim().substring(0, 500);
                const urlLower = url.toLowerCase();

                let signedIn = true;
                if (urlLower.includes('login') || urlLower.includes('signin')) {
                    signedIn = false;
                } else if (/sign in to clip|sign in \\/ up/i.test(bodyText)) {
                    signedIn = false;
                } else {
                    const hasSignInBtn = Array.from(document.querySelectorAll('button, a, span'))
                        .some(el => {
                            const t = (el.textContent || '').trim().toLowerCase();
                            return (t === 'sign in' || t === 'sign in / register' || t === 'log in') && el.offsetParent !== null;
                        });
                    if (hasSignInBtn) signedIn = false;
                }
                return JSON.stringify({ url, signedIn, bodySnippet: bodyText.substring(0, 300) });
            }
            """)
            import json as _json
            page_state = _json.loads(page_state_raw) if isinstance(page_state_raw, str) else page_state_raw
            session_signed_in = page_state.get('signedIn', True)
            diag_msg = (
                f"PRE-FLIGHT DIAGNOSTIC: url={page_state.get('url')} | "
                f"signed_in={session_signed_in} | "
                f"body_snippet={repr(page_state.get('bodySnippet', '')[:200])}"
            )
            logger.info(diag_msg)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{diag_msg}\n")
        except Exception as e:
            logger.warning(f"[safeway_run_pre_flight] Page state check failed (non-critical): {e}")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT: Page state check failed: {e}\n")

        if not session_signed_in:
            err_msg = (
                "❌ SAFEWAY SESSION EXPIRED — Not signed in.\n"
                "All coupons will fail to clip until you refresh the session.\n"
                "Fix: Run  uv run backend/automation/login_helper.py  and log in manually."
            )
            logger.error(err_msg)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{err_msg}\n")
            raise RuntimeError(
                "❌ Safeway session expired — not signed in. "
                "Run `uv run backend/automation/login_helper.py` and log in manually."
            )

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
                    f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                    json={
                        "model": config.LLM_MODEL,
                        "messages": [{"role": "user", "content": f"{extraction_system}\n\nPrompt: {prompt}"}],
                        "stream": False,
                        "temperature": 0
                    },
                    timeout=config.LLM_TIMEOUT
                )
                ext_json = extraction_resp.json()
                raw_content = ""
                if "choices" in ext_json and len(ext_json["choices"]) > 0:
                    raw_content = ext_json["choices"][0].get("message", {}).get("content", "{}")
                elif "message" in ext_json:
                    raw_content = ext_json.get("message", {}).get("content", "{}")
                data = json.loads(clean_json_text(raw_content))
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
                "RULES:\n"
                "1. NEVER pick 'Special Offers' if a specific food category is available.\n"
                "2. If nothing fits, use 'NONE' as category.\n"
                "3. Crucially, items that are typically sold frozen (e.g., ice cream, frozen meals, frozen pizza, popsicles, frozen waffles, frozen vegetables, frozen fruit) MUST be categorized under 'Frozen Foods' (or similar frozen category) rather than their ingredient-based category (like 'Dairy', 'Meat', or 'Produce').\n"
                f"Available Categories: {', '.join(available_categories)}"
            )
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    selector_resp = await client.post(
                        f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                        json={
                            "model": config.LLM_MODEL,
                            "messages": [{"role": "user", "content": f"{selector_system}\n\nItems: {', '.join(items)}"}],
                            "stream": False,
                            "temperature": 0
                        },
                        timeout=config.LLM_TIMEOUT
                    )
                    sel_json = selector_resp.json()
                    raw_content = ""
                    if "choices" in sel_json and len(sel_json["choices"]) > 0:
                        raw_content = sel_json["choices"][0].get("message", {}).get("content", "{}")
                    elif "message" in sel_json:
                        raw_content = sel_json.get("message", {}).get("content", "{}")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: Categorization response: {raw_content}\n")
                    
                    data = json.loads(clean_json_text(raw_content))
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
                logger.error(f"[safeway_run_pre_flight] Multi-Category mapping failed: {type(e).__name__}: {e}")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Multi-Category mapping failed: {type(e).__name__}: {e}\n")

        # Fallback: if mapping failed, process each item individually under empty category
        # (Do NOT concatenate all items into one keyword — that produces unsearchable strings)
        if not item_category_map:
            for item in items:
                if "" not in item_category_map:
                    item_category_map[""] = []
                item_category_map[""].append(item)

        # 4. Scrape deals AND clip coupons per category
        # Build a concise per-item summary (instead of raw card dumps that get truncated)
        item_results = []  # list of dicts: {item, category, clipped: [], already: [], no_match: bool}
        is_first_category = True
        for category, cat_items in item_category_map.items():
            if category:
                # Reset page to clean state before each new category to prevent
                # filter stacking or stale page state from previous category's clipping
                if not is_first_category:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: Resetting page before applying '{category}' filter...\n")
                    await page.goto(target_url)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=30000)
                    except Exception:
                        pass
                    await _asyncio.sleep(5)
                
                with open(log_path, "a", encoding="utf-8") as f: 
                    f.write(f"PRE-FLIGHT: Applying category filter '{category}' for items: {cat_items}...\n")
                filter_result = await safeway_filter_category(category, browser)
                
                # If filter failed, log it and skip this category's items
                if filter_result.startswith("Failure"):
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: ⚠️ Category filter FAILED for '{category}': {filter_result}\n")
                    for kw in cat_items:
                        item_results.append({
                            "item": kw,
                            "category": category,
                            "clipped": [],
                            "already": [],
                            "no_button": [],
                            "no_match": True,
                            "llm_error": False,
                            "filter_error": True,
                            "scrape_error": False
                        })
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"PRE-FLIGHT CLIP RESULT: Skipped '{kw}' — category filter for '{category}' failed.\n")
                    is_first_category = False
                    continue
                
                await _asyncio.sleep(3)
                
                # Click "Show More" / "Load More" button in the deals container to load cards
                for show_more_attempt in range(5):  # max 5 clicks
                    show_more_clicked = await page.evaluate("""
                    () => {
                        // Look for deals pagination button in main content area (ignore sidebar filter accordions)
                        const mainArea = document.querySelector('main, [role="main"], #main-content') || document.body;
                        const candidates = Array.from(mainArea.querySelectorAll('button, a, [role="button"]'))
                            .filter(el => el.offsetParent !== null && !el.closest('[class*="sidebar"], [class*="filter"], nav'));
                        for (const el of candidates) {
                            const text = (el.textContent || '').trim().toLowerCase();
                            if (text === 'show more' || text === 'load more' || text === 'load more deals' 
                                || text === 'view more' || text === 'see more deals') {
                                el.scrollIntoView({block: 'center'});
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }
                    """)
                    if not show_more_clicked:
                        break
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: Clicked 'Show More' button (attempt {show_more_attempt + 1})\n")
                    await _asyncio.sleep(2)
                
                is_first_category = False
            
            # Scrape clean product titles for all cards in this category once
            js_code = """
            () => {
                INSERT_SELECTORS_HERE
                let cards = [];
                for (const sel of selectors) {
                    try {
                        const els = Array.from(document.querySelectorAll(sel))
                            .filter(el => el.offsetParent !== null);
                        if (els.length > 0) { cards = els; break; }
                    } catch(e) {}
                }

                function cleanProductTitle(card) {
                    // 1. Try dedicated product title/name elements
                    const titleSelectors = [
                        '[class*="product-title"]', '[class*="deal-title"]', '[class*="offer-title"]',
                        '[class*="coupon-title"]', '[class*="item-title"]', '[class*="product-name"]',
                        'h3', 'h4', '[data-qa*="title"]', '[data-qa*="name"]',
                        '[class*="description"]'
                    ];
                    for (const sel of titleSelectors) {
                        try {
                            const el = card.querySelector(sel);
                            if (el) {
                                const t = (el.innerText || el.textContent || '').trim();
                                if (t.length >= 3 && !t.startsWith('$') && !t.toLowerCase().includes('expires') && !t.toLowerCase().includes('clip')) {
                                    return t.replace(/\\n/g, ' ').substring(0, 120);
                                }
                            }
                        } catch(e) {}
                    }
                    
                    // 2. Try product image alt text (Safeway deal images usually have alt="Brand Product Name")
                    try {
                        const img = card.querySelector('img[alt]');
                        if (img) {
                            const alt = (img.getAttribute('alt') || '').trim();
                            const altLow = alt.toLowerCase();
                            if (alt.length >= 4 && !altLow.includes('safeway') && !altLow.includes('coupon') && !altLow.includes('logo') && !altLow.includes('icon')) {
                                return alt.substring(0, 120);
                            }
                        }
                    } catch(e) {}
                    
                    // 3. Fallback: Parse card innerText lines, stripping prices, dates, button text
                    const lines = (card.innerText || '').split('\\n').map(l => l.trim()).filter(Boolean);
                    const candidateLines = lines.filter(l => {
                        const low = l.toLowerCase();
                        if (low.startsWith('$') || /^\\d+(\\.\\d+)?\\s*(off|each|for)/i.test(low)) return false;
                        if (low.includes('expires') || low.includes('valid through') || low.includes('good through')) return false;
                        if (low.includes('offer details') || low.includes('clip') || low.includes('clipped') || low.includes('add to card') || low.includes('load to card')) return false;
                        if (low.includes('one time use') || low.includes('unlimited use') || low.includes('when you buy') || low.includes('terms apply')) return false;
                        return l.length >= 3;
                    });
                    
                    if (candidateLines.length > 0) {
                        return candidateLines.slice(0, 2).join(' | ').substring(0, 120);
                    }
                    
                    return (card.innerText || '').replace(/\\n/g, ' | ').substring(0, 100);
                }
                
                return JSON.stringify(cards.map(c => cleanProductTitle(c)));
            }
            """.replace("INSERT_SELECTORS_HERE", CARD_SELECTORS_JS)

            card_texts_raw = await page.evaluate(js_code)
            card_texts = json.loads(card_texts_raw)

            if not card_texts:
                _log_to_file(log_path, f"PRE-FLIGHT WARNING: No cards found on page in '{category or 'General'}'")
                for kw in cat_items:
                    item_results.append({
                        "item": kw,
                        "category": category or "General",
                        "clipped": [],
                        "already": [],
                        "no_button": [],
                        "no_match": True,
                        "llm_error": False,
                        "filter_error": False,
                        "scrape_error": True
                    })
                continue

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT: Found {len(card_texts)} cards in '{category or 'General'}'. Evaluating {len(cat_items)} item(s) simultaneously: {cat_items}...\n")

            # Single pass LLM matching for all target items in this category
            matches_by_item = await _llm_match_cards_for_category(card_texts, cat_items, category, log_path)

            for kw in cat_items:
                result_entry = {
                    "item": kw,
                    "category": category or "General",
                    "clipped": [],
                    "already": [],
                    "no_button": [],
                    "no_match": False,
                    "llm_error": False,
                    "filter_error": False,
                    "scrape_error": False
                }

                if matches_by_item is None:
                    result_entry["llm_error"] = True
                    clip_result = f"LLM ERROR: Model failed while matching '{kw}' in '{category or 'General'}'."
                else:
                    matching_indices = matches_by_item.get(kw, [])
                    if matching_indices:
                        seen_texts = {}
                        unique_indices = []
                        for idx in matching_indices:
                            if idx < len(card_texts):
                                dedup_key = card_texts[idx][:150].strip().lower()
                                if dedup_key not in seen_texts:
                                    seen_texts[dedup_key] = idx
                                    unique_indices.append(idx)

                        if len(unique_indices) < len(matching_indices):
                            _log_to_file(log_path, f"PRE-FLIGHT DEDUP: Reduced {len(matching_indices)} matches to {len(unique_indices)} unique cards for '{kw}'")
                            logger.info(f"[safeway_run_pre_flight] Dedup: {len(matching_indices)} -> {len(unique_indices)} unique for '{kw}'")

                        clip_result, clip_results = await safeway_clip_by_indices(browser, matching_indices, keyword=kw)

                        seen_descs = set()
                        for r in clip_results:
                            status = r['status']
                            desc = r['cardText']
                            if status == 'clicked':
                                desc = _clean_deal_description(desc[:250])
                                desc_key = desc[:100].lower()
                                if desc_key not in seen_descs:
                                    seen_descs.add(desc_key)
                                    result_entry["clipped"].append(desc)
                            elif status == 'already_clipped':
                                desc = _clean_deal_description(desc[:250])
                                desc_key = desc[:100].lower()
                                if desc_key not in seen_descs:
                                    seen_descs.add(desc_key)
                                    result_entry["already"].append(desc)
                            elif status == 'no_button':
                                desc = _clean_deal_description(desc[:250])
                                desc_key = desc[:100].lower()
                                if desc_key not in seen_descs:
                                    seen_descs.add(desc_key)
                                    result_entry["no_button"].append(desc)
                    else:
                        result_entry["no_match"] = True
                        clip_result = f"LLM found no semantic matches for '{kw}' among {len(card_texts)} cards in '{category or 'General'}'."

                _log_to_file(log_path, f"PRE-FLIGHT CLIP RESULT: {clip_result}")
                logger.info(f"[safeway_run_pre_flight] Clip result for '{kw}': {clip_result.splitlines()[0] if clip_result else 'empty'}")
                item_results.append(result_entry)

        # Build concise summary for agent hand-off
        if not item_results:
            return "No deals found for the requested items."
        
        from collections import defaultdict
        category_groups = defaultdict(list)
        for r in item_results:
            cat = r["category"] or "General Deals"
            category_groups[cat].append(r)
            
        summary_lines = ["# 🛒 Safeway Coupon Results"]
        total_clipped = 0
        total_already = 0
        total_no_button = 0
        not_found_items = []
        failed_items = []
        
        for category, results in category_groups.items():
            category_has_results = False
            for r in results:
                has_results = r["clipped"] or r["already"] or r["no_button"]
                is_error = r.get("filter_error") or r.get("scrape_error") or r.get("llm_error")
                if r.get("filter_error"):
                    failed_items.append((r["item"], "Category Filter Failure"))
                elif r.get("scrape_error"):
                    failed_items.append((r["item"], "Scraping Failure"))
                elif r.get("llm_error"):
                    failed_items.append((r["item"], "LLM Matching Failure"))
                elif not has_results and r["no_match"] and not is_error:
                    not_found_items.append((r["item"], r["category"]))
                else:
                    category_has_results = True
            
            if not category_has_results:
                continue
                
            emoji = CATEGORY_EMOJIS.get(category, "📦")
            summary_lines.append(f"\n### {emoji} {category}")
            
            for r in results:
                has_results = r["clipped"] or r["already"] or r["no_button"]
                if not has_results:
                    continue
                
                summary_lines.append(f"\n#### **{r['item'].upper()}**")
                
                if r["clipped"]:
                    total_clipped += len(r["clipped"])
                    summary_lines.append(f"* **✅ Clipped {len(r['clipped'])} coupon(s):**")
                    for i, desc in enumerate(r["clipped"], 1):
                        formatted_desc = _format_deal_markdown(desc)
                        summary_lines.append(f"  {i}. {formatted_desc}")
                if r["already"]:
                    total_already += len(r["already"])
                    summary_lines.append(f"* **⏭️ Already clipped {len(r['already'])} coupon(s):**")
                    for i, desc in enumerate(r["already"], 1):
                        formatted_desc = _format_deal_markdown(desc)
                        summary_lines.append(f"  {i}. {formatted_desc}")
                if r["no_button"]:
                    total_no_button += len(r["no_button"])
                    summary_lines.append(f"* **➖ Auto-sale (no clip needed): {len(r['no_button'])}**")
                    for i, desc in enumerate(r["no_button"], 1):
                        formatted_desc = _format_deal_markdown(desc)
                        summary_lines.append(f"  {i}. {formatted_desc}")
        
        if not_found_items:
            summary_lines.append("\n### ❌ No Coupons Found")
            for item, cat in not_found_items:
                summary_lines.append(f"- **{item.upper()}** (searched in {cat})")
        
        if failed_items:
            summary_lines.append("\n### ⚠️ Failed Searches")
            for item, reason in failed_items:
                summary_lines.append(f"- **{item.upper()}** ({reason})")
        
        summary_lines.append("\n***")
        
        has_any_errors = len(failed_items) > 0
        if has_any_errors:
            status_text = "FAILED (encountered errors checking some items)"
        else:
            status_text = "COMPLETED"
            
        summary_lines.append(f"📊 **SUMMARY:** {len(item_results)} items searched | {total_clipped} clipped | {total_already} already clipped | {total_no_button} auto-sale | {len(not_found_items)} not found | {len(failed_items)} failed")
        summary_lines.append(f"\n⚠️ Task Status: {status_text}")
        
        return "\n".join(summary_lines)

    except Exception as e:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT ERROR: {e}\n")
        return ""
