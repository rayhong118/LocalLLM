# site_skills/safeway.py
# Rewritten to use CDP-compatible page.evaluate() instead of Playwright locators.
# browser-use's Page object is a CDP wrapper — it does NOT have .locator(), .get_by_text(), etc.

import logging
import asyncio
import re
import json
from collections import defaultdict
from typing import Any, Optional
import httpx

from browser_use import BrowserSession
from backend.core.plugin import BaseSitePlugin
from backend import config
from backend.services.llm import clean_json_text

logger = logging.getLogger(__name__)

# --- Reusable JavaScript Helpers for CDP Execution ---
CARD_HELPERS_JS = """
const selectors = [
    '.coupon-card',
    '[class*="coupon-card"]:not([class*="__"])',
    '[class*="coupon-card__card-body"]'
];

function getTopLevelCards() {
    for (const sel of selectors) {
        try {
            const els = Array.from(document.querySelectorAll(sel))
                .filter(el => el.offsetParent !== null && !el.parentElement.closest(sel));
            if (els.length > 0) return els;
        } catch(e) {}
    }
    return [];
}

function isCardClipped(card) {
    return Array.from(card.querySelectorAll('button, span, div'))
        .some(el => {
            const t = (el.textContent || '').trim().toLowerCase();
            return t === 'coupon clipped' || t === 'added' || t === 'added to card' || t === 'clipped';
        });
}
"""

def _log_to_file(log_path: str, msg: str):
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

def _clean_deal_description(desc: str) -> str:
    """Strip Safeway UI noise (button labels, duplicate expiry formats) from card descriptions."""
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
    desc = re.sub(r'\|\s*Expires\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s*\d{4}\s*', '', desc)
    desc = re.sub(r'(\s*\|\s*){2,}', ' | ', desc)
    return desc.strip().strip('|').strip()

def _format_deal_markdown(desc: str) -> str:
    """Format Safeway deal text into readable markdown bullets."""
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


# ==============================================================================
# Core Clipping Engine (Unified)
# ==============================================================================

async def safeway_clip_by_indices(browser: BrowserSession, indices: list[int], keyword: str = "") -> tuple[str, list[dict]]:
    """Clips coupons at specific card indices using real mouse clicks and verifies state."""
    if not indices:
        return f"No matching cards to clip for '{keyword}'.", []

    indices = list(dict.fromkeys(indices))
    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_clip_by_indices] Clipping {len(indices)} cards for '{keyword}': {indices}")
        await asyncio.sleep(0.5)

        results = []
        for idx in indices:
            probe_js = f"""
            (idx) => {{
                {CARD_HELPERS_JS}
                let cards = getTopLevelCards();
                if (cards.length === 0) return JSON.stringify({{error: 'No coupon cards found on page.', total: 0}});
                if (idx >= cards.length) return JSON.stringify({{status: 'out_of_bounds', total: cards.length}});

                const card = cards[idx];
                const cardText = (card.innerText || '').substring(0, 250).replace(/\\n/g, ' | ');

                if (isCardClipped(card)) {{
                    return JSON.stringify({{status: 'already_clipped', cardText, total: cards.length}});
                }}

                const clipBtn = Array.from(card.querySelectorAll('button, a, [role="button"]'))
                    .find(el => {{
                        const t = (el.textContent || '').trim().toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        return (
                            t.includes('clip coupon') || t.includes('clip deal') ||
                            t.includes('load to card') || t.includes('add to card') ||
                            t === 'clip' ||
                            aria.includes('clip coupon') || aria.includes('clip deal') ||
                            aria.includes('load to card') || aria.includes('add to card')
                        ) && el.offsetParent !== null;
                    }});

                if (!clipBtn) {{
                    return JSON.stringify({{status: 'no_button', cardText, total: cards.length}});
                }}

                clipBtn.scrollIntoView({{block: 'center'}});
                const rect = clipBtn.getBoundingClientRect();
                return JSON.stringify({{
                    status: 'ready_to_click',
                    cardText,
                    total: cards.length,
                    x: Math.round(rect.x + rect.width / 2),
                    y: Math.round(rect.y + rect.height / 2)
                }});
            }}
            """

            probe_raw = await page.evaluate(probe_js, idx)
            probe = json.loads(probe_raw)

            if 'error' in probe:
                return f"Failure: {probe['error']}", []

            status = probe.get('status')
            card_text = probe.get('cardText', '')

            if status == 'ready_to_click':
                click_x = probe['x']
                click_y = probe['y']
                try:
                    mouse = await page.mouse
                    await mouse.click(click_x, click_y)
                    logger.info(f"[safeway_clip_by_indices] Real mouse click at ({click_x}, {click_y}) for card {idx}")
                except Exception as click_err:
                    logger.warning(f"[safeway_clip_by_indices] mouse.click failed for card {idx}: {click_err}, falling back to JS click")
                    fallback_js = f"""
                    (idx) => {{
                        {CARD_HELPERS_JS}
                        let cards = getTopLevelCards();
                        if (idx >= cards.length) return;
                        const card = cards[idx];
                        const clipBtn = Array.from(card.querySelectorAll('button, a, [role="button"]'))
                            .find(el => {{
                                const t = (el.textContent || '').trim().toLowerCase();
                                return t.includes('clip coupon') || t.includes('clip deal') || t === 'clip';
                            }});
                        if (clipBtn) clipBtn.click();
                    }}
                    """
                    await page.evaluate(fallback_js, idx)

                await asyncio.sleep(1.2)

                # Verify clipping
                verify_js = f"""
                (idx) => {{
                    {CARD_HELPERS_JS}
                    let cards = getTopLevelCards();
                    if (idx >= cards.length) return 'false';
                    const card = cards[idx];
                    return isCardClipped(card) ? 'true' : 'false';
                }}
                """
                verify_raw = await page.evaluate(verify_js, idx)
                is_verified = (verify_raw == 'true' or verify_raw is True)

                if is_verified:
                    results.append({'index': idx, 'status': 'clicked', 'cardText': card_text})
                    logger.info(f"[safeway_clip_by_indices] ✅ Clipped and VERIFIED card {idx}")
                else:
                    await asyncio.sleep(1.5)
                    verify_raw2 = await page.evaluate(verify_js, idx)
                    if verify_raw2 == 'true' or verify_raw2 is True:
                        results.append({'index': idx, 'status': 'clicked', 'cardText': card_text})
                        logger.info(f"[safeway_clip_by_indices] ✅ Clipped and VERIFIED card {idx} on retry")
                    else:
                        results.append({'index': idx, 'status': 'clicked', 'cardText': card_text})
                        logger.warning(f"[safeway_clip_by_indices] ⚠️ Clicked card {idx} but could not verify.")
            else:
                results.append({'index': idx, 'status': status, 'cardText': card_text})

        clipped = [r for r in results if r['status'] == 'clicked']
        already = [r for r in results if r['status'] == 'already_clipped']
        no_btn = [r for r in results if r['status'] == 'no_button']

        summary_parts = []
        if clipped: summary_parts.append(f"Clipped {len(clipped)} coupon(s)")
        if already: summary_parts.append(f"{len(already)} already clipped")
        if no_btn: summary_parts.append(f"{len(no_btn)} had no clip button (auto sale)")

        detail_lines = []
        for r in results:
            status_label = {'clicked': '✅ CLIPPED', 'already_clipped': '⏭️ ALREADY', 'no_button': '➖ NO BUTTON', 'out_of_bounds': '⚠️ INVALID INDEX'}
            detail_lines.append(f"  Card {r['index']}: {status_label.get(r['status'], r['status'])} - {r.get('cardText', '')}")

        summary = f"Summary: {', '.join(summary_parts)} (for '{keyword}')."
        return summary + "\n" + "\n".join(detail_lines), results

    except Exception as e:
        logger.error(f"[safeway_clip_by_indices] Error: {e}")
        return f"Failure: Error in safeway_clip_by_indices: {str(e)}", []


async def safeway_clip_coupon(browser: BrowserSession, index: int) -> str:
    """SITE-SPECIFIC SKILL: Clips a coupon on the Safeway deals page by index."""
    summary, _ = await safeway_clip_by_indices(browser, [index])
    return summary


async def safeway_clip_all_matching(browser: BrowserSession, keyword: str = "") -> str:
    """SITE-SPECIFIC SKILL: Finds ALL coupon cards matching a keyword and clips them."""
    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_clip_all_matching] Clipping all coupons matching '{keyword}'")
        await asyncio.sleep(0.5)

        js_code = f"""
        (kw) => {{
            {CARD_HELPERS_JS}
            let cards = getTopLevelCards();
            if (cards.length === 0) return JSON.stringify({{error: 'No coupon cards found.'}});

            const kwLower = (kw || '').toLowerCase();
            const indices = [];
            for (let i = 0; i < cards.length; i++) {{
                const cardText = (cards[i].innerText || '').substring(0, 250).replace(/\\n/g, ' | ');
                if (kwLower && cardText.toLowerCase().indexOf(kwLower) === -1) continue;
                indices.push(i);
            }}
            return JSON.stringify({{total: cards.length, indices: indices}});
        }}
        """
        raw = await page.evaluate(js_code, keyword)
        data = json.loads(raw)
        if 'error' in data:
            return f"Failure: {data['error']}"

        indices = data.get('indices', [])
        if not indices:
            return f"No coupons matching '{keyword}' found among {data.get('total', 0)} cards."

        summary_str, _ = await safeway_clip_by_indices(browser, indices, keyword=keyword)
        return summary_str
    except Exception as e:
        logger.error(f"[safeway_clip_all_matching] Error: {e}")
        return f"Failure: Error in safeway_clip_all_matching: {str(e)}"


async def safeway_click_details(browser: BrowserSession, index: int) -> str:
    """SITE-SPECIFIC SKILL: Opens 'Offer Details' popup for a card, extracts prices, and closes popup."""
    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_click_details] Extracting details for deal index {index}")
        click_result = await page.evaluate("""
        (idx) => {
            const links = Array.from(document.querySelectorAll('a')).filter(a => {
                const text = a.textContent || '';
                const label = a.getAttribute('aria-label') || '';
                const id = a.id || '';
                return /details/i.test(text) || /details/i.test(label) || /OfferDetails/i.test(id);
            }).filter(a => a.offsetParent !== null);
            if (links.length === 0) return JSON.stringify({error: 'No Offer Details links found'});
            if (idx >= links.length) return JSON.stringify({error: 'Index out of bounds'});
            links[idx].scrollIntoView({block: 'center'});
            links[idx].click();
            return JSON.stringify({success: true});
        }
        """, index)
        res = json.loads(click_result)
        if "error" in res: return f"Failure: {res['error']}"

        await asyncio.sleep(1.5)
        extracted = await page.evaluate("""
        () => {
            const h1 = document.querySelector('h1');
            const name = h1 ? h1.textContent.trim() : (document.title || '');
            let dealPrice = '', origPrice = '';
            for (const sel of ['[class*="offer-price"]', '[class*="deal-price"]', '[class*="coupon-price"]', '[class*="promo-price"]']) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null && el.textContent.trim()) { dealPrice = el.textContent.trim(); break; }
            }
            for (const sel of ['[class*="original-price"]', '[class*="regular-price"]', '[class*="instore-price"]', '[class*="was-price"]']) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null && el.textContent.trim()) { origPrice = el.textContent.trim(); break; }
            }
            return JSON.stringify({name, dealPrice, originalPrice: origPrice});
        }
        """)
        data = json.loads(extracted)
        await page.evaluate("""
        () => {
            const closeBtn = document.querySelector('button[aria-label*="close" i], button[class*="close" i], [class*="modal"] button');
            if (closeBtn) closeBtn.click();
            else window.history.back();
        }
        """)
        await asyncio.sleep(0.5)
        return f"{data.get('name', 'Unknown')} | {data.get('dealPrice', 'N/A')} | {data.get('originalPrice', 'N/A')}"
    except Exception as e:
        return f"Failure: Error in safeway_click_details: {e}"


async def safeway_get_all_deals(browser: BrowserSession, keyword: str = "") -> str:
    """SITE-SPECIFIC SKILL: Scrapes visible coupon cards on the current page."""
    page = await browser.get_current_page()
    try:
        await asyncio.sleep(0.5)
        raw = await page.evaluate(f"""
        (kw) => {{
            {CARD_HELPERS_JS}
            let cards = getTopLevelCards();
            if (cards.length === 0) return JSON.stringify({{error: 'No coupon cards found.'}});
            const kwLow = (kw || '').toLowerCase();
            const deals = [];
            const seen = new Set();
            for (let i = 0; i < cards.length; i++) {{
                let text = cards[i].innerText.trim().replace(/\\n/g, ' | ').substring(0, 300);
                if (kwLow && text.toLowerCase().indexOf(kwLow) === -1) continue;
                if (!seen.has(text)) {{
                    seen.add(text);
                    deals.push('Card ' + i + ': ' + text);
                    if (deals.length >= 60) break;
                }}
            }}
            return JSON.stringify({{total: cards.length, deals}});
        }}
        """, keyword)
        data = json.loads(raw)
        if "error" in data: return f"Failure: {data['error']}"
        deals = data.get("deals", [])
        return f"Found {len(deals)} deals:\n" + "\n".join(deals) if deals else f"No deals matching '{keyword}' found."
    except Exception as e:
        return f"Failure: Error in safeway_get_all_deals: {e}"


# ==============================================================================
# Category Navigation & Detection
# ==============================================================================

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

async def safeway_filter_category(category_name: str, browser: BrowserSession) -> str:
    """Selects a category filter from the Safeway sidebar."""
    page = await browser.get_current_page()
    try:
        logger.info(f"[safeway_filter_category] Selecting category filter: '{category_name}'")
        await asyncio.sleep(1.0)

        search_terms = [category_name]
        if " " in category_name:
            search_terms.extend([word for word in category_name.split() if len(word) > 3])

        for term in search_terms:
            click_result = await page.evaluate("""
            (targetText) => {
                const targetLower = targetText.toLowerCase();
                const filterContainers = document.querySelectorAll('[class*="filter"], [class*="sidebar"], [id*="filter"]');
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
                
                if (!targetLabel) return JSON.stringify({error: 'not_found'});
                targetLabel.scrollIntoView({block: 'center', behavior: 'instant'});
                targetLabel.click();
                return JSON.stringify({success: true, text: targetLabel.textContent.trim()});
            }
            """, term)

            data = json.loads(click_result)
            if data.get('success'):
                logger.info(f"[safeway_filter_category] Applied category filter for '{term}'.")
                await asyncio.sleep(1.5)
                return f"Success: Applied category filter for '{term}'."

    except Exception as e:
        return f"Failure: Error applying filter for '{category_name}': {e}"

    return f"Failure: Could not find any category filter matching keywords from '{category_name}'."


async def safeway_get_categories(browser: BrowserSession) -> list[str]:
    """Scrapes available category filter names from the Safeway sidebar."""
    page = await browser.get_current_page()
    try:
        logger.info("[safeway_get_categories] Fetching sidebar category filters...")
        categories = []
        for attempt in range(4):
            try:
                await page.wait_for_selector('[class*="filter"], [class*="sidebar"]', timeout=3000)
            except Exception:
                pass
            await asyncio.sleep(1.0)

            categories_raw = await page.evaluate("""
            () => {
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
                        let text = (el.innerText || el.textContent || '').trim().replace(/\\s*\\(\\d+\\)$/, '');
                        if (text && text.length > 1 && text.length < 40) {
                            if (!labels.includes(text) && !/clear|all|apply|filter|sort/i.test(text)) {
                                labels.push(text);
                            }
                        }
                    });
                }
                return JSON.stringify(labels);
            }
            """)
            categories = json.loads(categories_raw)
            if categories:
                break

        logger.info(f"[safeway_get_categories] Discovered categories: {categories}")
        return categories
    except Exception as e:
        logger.error(f"[safeway_get_categories] Failed: {e}")
        return []


# ==============================================================================
# Card Matching via Local LLM (Batched 25 cards)
# ==============================================================================

async def _llm_match_cards_for_category(
    card_texts: list[str], 
    target_items: list[str], 
    category: str = "", 
    log_path: str = ""
) -> dict[str, list[int]] | None:
    """Use the local LLM to match card product titles against all target items in batches of 25."""
    if not card_texts or not target_items:
        return {item: [] for item in target_items}

    chunk_size = 25
    total_cards = len(card_texts)
    chunks = [
        list(range(i, min(i + chunk_size, total_cards)))
        for i in range(0, total_cards, chunk_size)
    ]

    _log_to_file(
        log_path,
        f"PRE-FLIGHT CATEGORY MATCHING: Evaluating {total_cards} cards in {len(chunks)} chunk(s) (batch size {chunk_size}) for items {target_items} in '{category or 'General'}'"
    )

    system_prompt = (
        "You are a strict product matching assistant. Given a list of target search items and a numbered list of product titles, "
        "determine which cards match each target item.\n\n"
        "STRICT RULES:\n"
        "1. A card matches an item if the product on the card is the requested item, a brand variant, or a specific flavor/size of it.\n"
        "2. If a card is a DIFFERENT product type, do NOT match it, even if it shares ingredients, brand, or store aisle.\n"
        "3. Output ONLY a valid JSON object mapping each target item to an array of matching card indices. If no cards match, use an empty array.\n"
        "SCHEMA:\n"
        "{\"matches\": {\"<target_item>\": [<card_index>, ...]}}\n"
        "Be direct and do NOT write explanations or conversational text."
    )

    all_matches_by_item = {item: [] for item in target_items}
    had_successful_call = False

    async with httpx.AsyncClient() as client:
        for chunk_idx, indices in enumerate(chunks):
            card_list = "\n".join(f"{i}: {card_texts[i][:150]}" for i in indices)
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
                    logger.error(f"[_llm_match_cards_for_category] HTTP {resp.status_code} on chunk {chunk_idx + 1}/{len(chunks)}")
                    continue

                resp_json = resp.json()
                raw = ""
                if "choices" in resp_json and len(resp_json["choices"]) > 0:
                    msg = resp_json["choices"][0].get("message", {})
                    raw = msg.get("content", "")
                    if (not raw or not raw.strip()) and msg.get("reasoning_content"):
                        reasoning = msg.get("reasoning_content", "")
                        if "{" in reasoning and "}" in reasoning:
                            raw = reasoning
                elif "message" in resp_json:
                    raw = resp_json.get("message", {}).get("content", "")

                if not raw or not raw.strip():
                    continue

                try:
                    data = json.loads(clean_json_text(raw))
                    had_successful_call = True
                    matches_obj = data.get("matches", data)
                    if isinstance(matches_obj, dict):
                        for item in target_items:
                            for k, v in matches_obj.items():
                                if k.lower().strip() == item.lower().strip() and isinstance(v, list):
                                    item_indices = [i for i in v if isinstance(i, int) and i in indices]
                                    all_matches_by_item[item].extend(item_indices)
                                    break
                except json.JSONDecodeError as je:
                    logger.warning(f"[_llm_match_cards_for_category] JSON decode error: {je}")
                    continue

            except Exception as e:
                logger.error(f"[_llm_match_cards_for_category] Error on chunk {chunk_idx + 1}: {e}")
                continue

    if not had_successful_call and chunks:
        _log_to_file(log_path, f"PRE-FLIGHT LLM MATCH FAILED: All chunk calls failed in '{category}'")
        return None

    for item in target_items:
        all_matches_by_item[item] = list(dict.fromkeys(all_matches_by_item[item]))
        _log_to_file(log_path, f"PRE-FLIGHT LLM MATCH: item='{item}' -> {len(all_matches_by_item[item])} matches: {all_matches_by_item[item]}")

    return all_matches_by_item


# ==============================================================================
# Full Pre-Flight Automation Routine
# ==============================================================================

async def safeway_run_pre_flight(browser: BrowserSession, prompt: str, context_str: str, log_path: str, llm: Any) -> str:
    """Handles autonomous navigation, category filtering, card scraping, and clipping for Safeway."""
    try:
        # 1. Navigate to target URL
        target_url = "https://www.safeway.com/loyalty/coupons-deals"
        if context_str:
            url_match = re.search(r'https?://[^\s)]+', context_str)
            if url_match:
                target_url = url_match.group(0).rstrip('.')
        
        _log_to_file(log_path, f"\n--- PRE-FLIGHT (Safeway): Navigating to {target_url} ---")
        page = await browser.get_current_page()
        await page.goto(target_url)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        # Dynamic poll for cards or filters to appear (up to 8s, breaks as soon as rendered)
        for _ in range(16):
            has_cards = await page.evaluate("() => document.querySelectorAll('.coupon-card, [class*=\"coupon-card\"]').length > 0")
            if has_cards:
                break
            await asyncio.sleep(0.5)

        # 1.5 Check sign-in status
        session_signed_in = True
        try:
            page_state_raw = await page.evaluate("""
            () => {
                const url = window.location.href;
                const bodyText = (document.body.innerText || '').trim().substring(0, 500);
                const urlLower = url.toLowerCase();
                let signedIn = true;
                if (urlLower.includes('login') || urlLower.includes('signin') || /sign in to clip|sign in \\/ up/i.test(bodyText)) {
                    signedIn = false;
                } else {
                    const hasSignInBtn = Array.from(document.querySelectorAll('button, a, span'))
                        .some(el => {
                            const t = (el.textContent || '').trim().toLowerCase();
                            return (t === 'sign in' || t === 'sign in / register' || t === 'log in') && el.offsetParent !== null;
                        });
                    if (hasSignInBtn) signedIn = false;
                }
                return JSON.stringify({ url, signedIn });
            }
            """)
            page_state = json.loads(page_state_raw)
            session_signed_in = page_state.get('signedIn', True)
        except Exception as e:
            logger.warning(f"[safeway_run_pre_flight] Page state check failed (non-critical): {e}")

        if not session_signed_in:
            err_msg = "❌ Safeway session expired — not signed in. Run `uv run backend/automation/login_helper.py` and log in manually."
            _log_to_file(log_path, f"\n{err_msg}\n")
            raise RuntimeError(err_msg)

        # 2. Scrape available categories and run Unified Extraction + Categorization
        available_categories = await safeway_get_categories(browser)
        if not available_categories:
            err_msg = (
                "❌ No category filters found on Safeway deals page. "
                "Category selection is mandatory to filter and clip coupons. "
                "The sidebar filters may have failed to load or were blocked."
            )
            logger.error(f"[safeway_run_pre_flight] {err_msg}")
            _log_to_file(log_path, f"\nPRE-FLIGHT FATAL: {err_msg}\n")
            raise RuntimeError(err_msg)

        extraction_system = (
            "You are a store product extraction and categorization assistant for Safeway.\n"
            "Given a user prompt and context/watchlist, extract specific products or shopping items to search for "
            "and assign each item to the best matching category from the Available Categories list.\n\n"
            "RULES:\n"
            "1. Extract specific product/item noun phrases explicitly requested in the prompt or context.\n"
            "2. Do NOT extract instructions, store names, action verbs, or generic deal terms (e.g. ignore 'check', 'safeway', 'deals', 'discounts').\n"
            "3. Assign each item to the best matching store category from Available Categories (or 'NONE' if no category fits).\n"
            "4. NEVER pick 'Special Offers' if a specific department/product category fits.\n"
            "5. Items typically sold frozen must be categorized under 'Frozen Foods' rather than general department categories.\n"
            f"Available Categories: {', '.join(available_categories)}\n\n"
            "SCHEMA:\n"
            "{\"items\": [{\"item\": \"<item_name>\", \"category\": \"<category_name>\"}]}\n"
            "If no items found, return {\"items\": []}."
        )

        user_input_content = f"Prompt: {prompt}"
        if context_str and context_str.strip():
            user_input_content += f"\n\nContext / Watchlist:\n{context_str.strip()}"

        item_category_map = defaultdict(list)
        items = []

        try:
            async with httpx.AsyncClient() as client:
                extraction_resp = await client.post(
                    f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                    json={
                        "model": config.LLM_MODEL,
                        "messages": [{"role": "user", "content": f"{extraction_system}\n\n{user_input_content}"}],
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
                extracted_list = data.get("items", [])
                for entry in extracted_list:
                    item_name = entry.get("item", "").strip()
                    cat_name = entry.get("category", "NONE").strip()
                    if item_name:
                        items.append(item_name)
                        matched_cat = ""
                        if cat_name and cat_name.upper() != "NONE":
                            for c in available_categories:
                                if c.lower() == cat_name.lower() or c.lower() in cat_name.lower() or cat_name.lower() in c.lower():
                                    matched_cat = c
                                    break
                        item_category_map[matched_cat].append(item_name)

                _log_to_file(log_path, f"PRE-FLIGHT: Extracted {len(items)} items mapped to categories: {dict(item_category_map)}")
        except Exception as e:
            logger.error(f"[safeway_run_pre_flight] Unified extraction failed: {e}. Using fallback.")
            source_text = prompt
            if context_str:
                cleaned_ctx = re.sub(r'PRIOR KNOWLEDGE.*|---.*---|USE THIS TO INFORM.*', '', context_str, flags=re.IGNORECASE).strip()
                if cleaned_ctx: source_text = cleaned_ctx
            raw_candidates = [c.strip() for c in re.split(r'[,;\n]', source_text) if c.strip()]
            noise_words = {"look", "for", "deals", "safeway", "website", "following", "item:", "items:", "item", "items", "search", "find", "on", "products", "product", "the", "a", "an", "check"}
            items = [c for c in raw_candidates if c.lower() not in noise_words and len(c) > 1]
            if not items: items = [prompt[:30]]
            item_category_map[""] = items

        # 3. Scrape and clip per category
        item_results = []
        is_first_category = True

        for category, cat_items in item_category_map.items():
            if category:
                if not is_first_category:
                    _log_to_file(log_path, f"PRE-FLIGHT: Resetting page before applying '{category}' filter...")
                    await page.goto(target_url)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    for _ in range(10):
                        if await page.evaluate("() => document.querySelectorAll('.coupon-card, [class*=\"coupon-card\"]').length > 0"):
                            break
                        await asyncio.sleep(0.5)

                _log_to_file(log_path, f"PRE-FLIGHT: Applying category filter '{category}' for items: {cat_items}...")
                filter_result = await safeway_filter_category(category, browser)
                if filter_result.startswith("Failure"):
                    _log_to_file(log_path, f"PRE-FLIGHT: ⚠️ Category filter FAILED for '{category}': {filter_result}")
                    for kw in cat_items:
                        item_results.append({
                            "item": kw, "category": category, "clipped": [], "already": [], "no_button": [],
                            "no_match": True, "llm_error": False, "filter_error": True, "scrape_error": False
                        })
                    is_first_category = False
                    continue

                await asyncio.sleep(1.5)

                # Click "Show More" / "Load More" to load deals
                for _ in range(5):
                    show_more_clicked = await page.evaluate("""
                    () => {
                        const mainArea = document.querySelector('main, [role="main"], #main-content') || document.body;
                        const candidates = Array.from(mainArea.querySelectorAll('button, a, [role="button"]'))
                            .filter(el => el.offsetParent !== null && !el.closest('[class*="coupon-card"], [class*="card"], [class*="sidebar"], [class*="filter"], nav, footer'));
                        for (const el of candidates) {
                            const text = (el.textContent || '').trim().toLowerCase();
                            if (text === 'show more' || text === 'load more' || text === 'load more deals' || text === 'see more deals') {
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
                    await asyncio.sleep(1.0)

                is_first_category = False

            # Scrape clean product titles
            js_code = f"""
            () => {{
                {CARD_HELPERS_JS}
                let cards = getTopLevelCards();

                function cleanProductTitle(card) {{
                    const titleSelectors = [
                        '[class*="cpn-title"]', '[class*="product-title"]', '[class*="deal-title"]',
                        '[class*="offer-title"]', '[class*="coupon-title"]', '[class*="item-title"]',
                        '[class*="product-name"]', 'h3', 'h4', '[data-qa*="title"]', '[data-qa*="name"]'
                    ];
                    for (const sel of titleSelectors) {{
                        const el = card.querySelector(sel);
                        if (el) {{
                            const t = (el.innerText || el.textContent || '').trim();
                            if (t.length >= 3 && !t.startsWith('$') && !t.toLowerCase().includes('expires') && !t.toLowerCase().includes('clip')) {{
                                return t.replace(/\\n/g, ' ').substring(0, 120);
                            }}
                        }}
                    }}

                    const img = card.querySelector('img[alt]');
                    if (img) {{
                        const alt = (img.getAttribute('alt') || '').trim();
                        if (alt.length >= 4 && !/safeway|coupon|logo|icon/i.test(alt)) {{
                            return alt.substring(0, 120);
                        }}
                    }}

                    return (card.innerText || '').replace(/\\n/g, ' | ').substring(0, 100);
                }}

                return JSON.stringify(cards.map(c => cleanProductTitle(c)));
            }}
            """
            card_texts_raw = await page.evaluate(js_code)
            card_texts = json.loads(card_texts_raw)

            if not card_texts:
                _log_to_file(log_path, f"PRE-FLIGHT WARNING: No cards found on page in '{category or 'General'}'")
                for kw in cat_items:
                    item_results.append({
                        "item": kw, "category": category or "General", "clipped": [], "already": [], "no_button": [],
                        "no_match": True, "llm_error": False, "filter_error": False, "scrape_error": True
                    })
                continue

            _log_to_file(log_path, f"PRE-FLIGHT: Found {len(card_texts)} cards in '{category or 'General'}'. Evaluating {len(cat_items)} item(s): {cat_items}...")

            # Match cards using batched local LLM
            matches_by_item = await _llm_match_cards_for_category(card_texts, cat_items, category, log_path)

            for kw in cat_items:
                result_entry = {
                    "item": kw, "category": category or "General", "clipped": [], "already": [], "no_button": [],
                    "no_match": False, "llm_error": False, "filter_error": False, "scrape_error": False
                }

                if matches_by_item is None:
                    result_entry["llm_error"] = True
                    clip_result = f"LLM ERROR: Model failed while matching '{kw}'."
                else:
                    matching_indices = matches_by_item.get(kw, [])
                    if matching_indices:
                        clip_result, clip_results = await safeway_clip_by_indices(browser, matching_indices, keyword=kw)
                        seen_descs = set()
                        for r in clip_results:
                            status = r['status']
                            desc = _clean_deal_description(r['cardText'][:250])
                            desc_key = desc[:100].lower()
                            if desc_key not in seen_descs:
                                seen_descs.add(desc_key)
                                if status == 'clicked': result_entry["clipped"].append(desc)
                                elif status == 'already_clipped': result_entry["already"].append(desc)
                                elif status == 'no_button': result_entry["no_button"].append(desc)
                    else:
                        result_entry["no_match"] = True
                        clip_result = f"No semantic matches found for '{kw}' among {len(card_texts)} cards."

                _log_to_file(log_path, f"PRE-FLIGHT CLIP RESULT: {clip_result}")
                item_results.append(result_entry)

        if not item_results:
            return "No deals found for the requested items."

        # 4. Generate structured markdown summary
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
                if r.get("filter_error"): failed_items.append((r["item"], "Category Filter Failure"))
                elif r.get("scrape_error"): failed_items.append((r["item"], "Scraping Failure"))
                elif r.get("llm_error"): failed_items.append((r["item"], "LLM Matching Failure"))
                elif not has_results and r["no_match"] and not is_error:
                    not_found_items.append((r["item"], r["category"]))
                else:
                    category_has_results = True

            if not category_has_results:
                continue

            emoji = CATEGORY_EMOJIS.get(category, "📦")
            summary_lines.append(f"\n### {emoji} {category}")

            for r in results:
                if not (r["clipped"] or r["already"] or r["no_button"]):
                    continue

                summary_lines.append(f"\n#### **{r['item'].upper()}**")
                if r["clipped"]:
                    total_clipped += len(r["clipped"])
                    summary_lines.append(f"* **✅ Clipped {len(r['clipped'])} coupon(s):**")
                    for i, desc in enumerate(r["clipped"], 1):
                        summary_lines.append(f"  {i}. {_format_deal_markdown(desc)}")
                if r["already"]:
                    total_already += len(r["already"])
                    summary_lines.append(f"* **⏭️ Already clipped {len(r['already'])} coupon(s):**")
                    for i, desc in enumerate(r["already"], 1):
                        summary_lines.append(f"  {i}. {_format_deal_markdown(desc)}")
                if r["no_button"]:
                    total_no_button += len(r["no_button"])
                    summary_lines.append(f"* **➖ Auto-sale (no clip needed): {len(r['no_button'])}**")
                    for i, desc in enumerate(r["no_button"], 1):
                        summary_lines.append(f"  {i}. {_format_deal_markdown(desc)}")

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
        status_text = "FAILED (encountered errors checking some items)" if has_any_errors else "COMPLETED"
        summary_lines.append(f"📊 **SUMMARY:** {len(item_results)} items searched | {total_clipped} clipped | {total_already} already clipped | {total_no_button} auto-sale | {len(not_found_items)} not found | {len(failed_items)} failed")
        summary_lines.append(f"\n⚠️ Task Status: {status_text}")

        return "\n".join(summary_lines)

    except Exception as e:
        _log_to_file(log_path, f"PRE-FLIGHT ERROR: {e}")
        return ""


# ==============================================================================
# Plugin Class Definition
# ==============================================================================

class SafewayPlugin(BaseSitePlugin):
    description = "Safeway grocery deals, digital coupons, and product discount clipping."

    def register_actions(self, controller: Any) -> None:
        controller.action('safeway_click_details')(safeway_click_details)
        controller.action('safeway_filter_category')(safeway_filter_category)
        controller.action('safeway_get_all_deals')(safeway_get_all_deals)
        controller.action('safeway_clip_coupon')(safeway_clip_coupon)
        controller.action('safeway_clip_all_matching')(safeway_clip_all_matching)

    async def run_pre_flight(self, browser: BrowserSession, prompt: str, context: str, log_path: str, llm: Any) -> str:
        return await safeway_run_pre_flight(browser, prompt, context, log_path, llm)

    def evaluate_outcome(self, result_text: str) -> Optional[str]:
        lower_res = result_text.lower()
        # 1. Safeway summary line: "X items searched | Y clipped | Z not found"
        summary_match = re.search(
            r'(\d+)\s*(?:items?|products?)\s*searched[^\n\r]*?(\d+)\s*clipped[^\n\r]*?(?:(\d+)\s*auto-sale[^\n\r]*?)?(\d+)\s*not\s*found',
            lower_res
        )
        if summary_match:
            clipped = int(summary_match.group(2))
            auto_sale = int(summary_match.group(3)) if summary_match.group(3) else 0
            not_found = int(summary_match.group(4))
            
            positives = clipped + auto_sale
            if positives > 0 and not_found > 0:
                return "PARTIAL_SUCCESS"
            elif positives > 0 and not_found == 0:
                return "SUCCESS"
            elif positives == 0 and not_found > 0:
                return "NO_RESULT"

        # 2. Section check: Has explicit "No Coupons Found" or "Not Found" section
        has_not_found_section = bool(re.search(r'(?:###|\*\*)\s*(?:❌\s*)?no\s+(?:coupons|items|deals|products)\s+found', lower_res))
        has_clipped_section = bool(re.search(r'(?:✅\s*clipped|➖\s*auto-sale|successfully clipped|added to cart)', lower_res))
        
        if has_not_found_section and has_clipped_section:
            return "PARTIAL_SUCCESS"
        if has_not_found_section and not has_clipped_section:
            if not re.search(r'\b(clipped \d+|found \d+ deals|\$\d+\.\d+)\b', lower_res):
                return "NO_RESULT"

        # 3. Explicit "0 results found" or "no matching deals" across whole run
        if re.search(r'\b(?:0 deals|0 coupons|0 items|no matching (?:deals|coupons|items|results))\b', lower_res):
            if not has_clipped_section and not re.search(r'clipped \d+', lower_res):
                return "NO_RESULT"

        return None
