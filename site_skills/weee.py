# site_skills/weee.py
# Rewritten to use CDP-compatible page.evaluate() instead of Playwright locators.
# browser-use's Page object is a CDP wrapper — it does NOT have .locator(), .get_by_text(), etc.

from browser_use import BrowserSession
from core.plugin import BaseSitePlugin
import logging
import asyncio
from typing import Any
import config
import re
import json

logger = logging.getLogger(__name__)

def _format_deal_markdown(desc: str) -> str:
    """Format Weee product details cleanly for final markdown reports."""
    parts = [p.strip() for p in desc.split('|') if p.strip()]
    if not parts:
        return desc
        
    title = ""
    weight = ""
    price = ""
    deal_price = ""
    link = ""
    discount = ""
    
    for p in parts:
        if p.startswith("Title:"):
            title = p.replace("Title:", "").strip()
        elif p.startswith("Weight:"):
            weight = p.replace("Weight:", "").strip()
        elif p.startswith("Price:"):
            price = p.replace("Price:", "").strip()
        elif p.startswith("Deal Price:"):
            deal_price = p.replace("Deal Price:", "").strip()
        elif p.startswith("Link:"):
            link = p.replace("Link:", "").strip()
        elif p.startswith("Discount:"):
            discount = p.replace("Discount:", "").strip()
            
    formatted = ""
    if title:
        formatted += f"**{title}**"
    if weight and weight != "N/A":
        formatted += f" ({weight})"
    if deal_price and deal_price != "N/A":
        formatted += f" - **Deal: {deal_price}**"
    if price and price != "N/A":
        formatted += f" (Reg: {price})"
    if discount:
        formatted += f" [{discount}]"
    if link and link != "N/A":
        formatted += f" - [Product Link]({link})"
        
    return formatted or desc

async def _llm_match_cards(card_texts: list[str], keyword: str, log_path: str = "") -> list[int]:
    """Use the local LLM to semantically match card texts against a user keyword."""
    if not card_texts or not keyword:
        return []

    import httpx

    card_list = "\n".join(f"{i}: {text[:120]}" for i, text in enumerate(card_texts))

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
                    "think": False,
                    "options": {"temperature": 0, "num_ctx": 8192}
                },
                timeout=config.LLM_TIMEOUT
            )
            
            if resp.status_code != 200:
                err_msg = f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
                logger.error(f"[_llm_match_cards] {err_msg}")
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT LLM MATCH FAILED: {err_msg}\n")
                return None
            
            resp_json = resp.json()
            raw = resp_json.get("message", {}).get("content", "")
            
            if not raw or not raw.strip():
                err_msg = f"Ollama returned empty content."
                logger.error(f"[_llm_match_cards] {err_msg}")
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT LLM MATCH FAILED: {err_msg}\n")
                return None
            
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as je:
                err_msg = f"Failed to parse LLM response as JSON: {je}. Raw content: {raw[:300]}"
                logger.error(f"[_llm_match_cards] {err_msg}")
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT LLM MATCH FAILED: {err_msg}\n")
                return None
            
            matches = data.get("matches", [])
            valid = [i for i in matches if isinstance(i, int) and 0 <= i < len(card_texts)]

            if log_path:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT LLM MATCH: keyword='{keyword}' -> {len(valid)} matches out of {len(card_texts)} cards: {valid}\n")
            logger.info(f"[_llm_match_cards] keyword='{keyword}' -> {len(valid)} matches: {valid}")
            return valid

    except httpx.TimeoutException:
        err_msg = f"Ollama request timed out after {config.LLM_TIMEOUT}s for keyword '{keyword}'"
        logger.error(f"[_llm_match_cards] {err_msg}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT LLM MATCH FAILED (TIMEOUT): {err_msg}\n")
        return None
    except httpx.ConnectError as ce:
        err_msg = f"Cannot connect to Ollama at localhost:11434: {ce}"
        logger.error(f"[_llm_match_cards] {err_msg}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT LLM MATCH FAILED (CONNECTION): {err_msg}\n")
        return None
    except Exception as e:
        err_msg = f"LLM matching failed for '{keyword}': {type(e).__name__}: {e}"
        logger.error(f"[_llm_match_cards] {err_msg}. Falling back to empty.")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT LLM MATCH FAILED: {err_msg}\n")
        return None


async def weee_add_to_favorites_by_indices(browser: BrowserSession, indices: list[int], keyword: str = ""):
    """SITE-SPECIFIC SKILL: Adds items at specific card indices to Weee favorites.
    Used with _llm_match_cards for semantic matching.
    """
    if not indices:
        return f"No matching cards to add to favorites for '{keyword}'."

    page = await browser.get_current_page()
    try:
        logger.info(f"[weee_add_to_favorites_by_indices] Adding {len(indices)} cards to favorites for '{keyword}': {indices}")
        await asyncio.sleep(1.0)

        result_raw = await page.evaluate("""
        (targetIndices) => {
            const cards = Array.from(document.querySelectorAll('a[aria-label^="weee "]')).filter(el => el.offsetParent !== null);
            if (cards.length === 0)
                return JSON.stringify({error: 'No product cards found on the page.'});

            const results = [];

            for (const idx of targetIndices) {
                if (idx >= cards.length) {
                    results.push({index: idx, status: 'out_of_bounds'});
                    continue;
                }

                const card = cards[idx];
                const label = card.getAttribute('aria-label') || '';
                
                // Parse title and ID
                let title = label.substring(5).trim();
                const words = title.split(' ');
                let productId = '';
                if (words.length > 0 && /^\d+$/.test(words[words.length - 1])) {
                    productId = words[words.length - 1];
                    words.pop();
                    title = words.join(' ');
                }

                let favBtn = null;

                // Strategy 1: Look inside the card's parent container for standard favorite buttons
                let parent = card.parentElement;
                for (let depth = 0; depth < 5; depth++) {
                    if (!parent) break;
                    const elements = Array.from(parent.querySelectorAll('button, [role="button"], a, svg, span, div'));
                    for (const el of elements) {
                        if (el === card) continue;
                        const text = (el.textContent || '').trim().toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const titleAttr = (el.getAttribute('title') || '').toLowerCase();
                        const className = (el.className || '').toString().toLowerCase();
                        const id = (el.id || '').toLowerCase();
                        
                        if (
                            aria.includes('favorite') || aria.includes('wishlist') || aria.includes('collect') || aria.includes('like') || aria.includes('heart') ||
                            titleAttr.includes('favorite') || titleAttr.includes('wishlist') || titleAttr.includes('collect') || titleAttr.includes('like') || titleAttr.includes('heart') ||
                            text.includes('favorite') || text.includes('wishlist') || text.includes('collect') || text.includes('like') || text.includes('heart') ||
                            className.includes('favorite') || className.includes('wishlist') || className.includes('collect') || className.includes('like') || className.includes('heart') || className.includes('fav-') || className.includes('-fav') ||
                            id.includes('favorite') || id.includes('wishlist') || id.includes('collect') || id.includes('like') || id.includes('heart')
                        ) {
                            favBtn = el;
                            break;
                        }
                    }
                    if (favBtn) break;
                    parent = parent.parentElement;
                }

                // Strategy 2: Look in the whole document for buttons close to the card containing heart icons
                if (!favBtn) {
                    const rect = card.getBoundingClientRect();
                    const buttons = Array.from(document.querySelectorAll('button, [role="button"], a')).filter(b => b.offsetParent !== null && b !== card);
                    let bestBtn = null;
                    let minDist = Infinity;
                    for (const btn of buttons) {
                        const btnRect = btn.getBoundingClientRect();
                        const dist = Math.hypot(
                            (rect.left + rect.width/2) - (btnRect.left + btnRect.width/2),
                            (rect.top + rect.height/2) - (btnRect.top + btnRect.height/2)
                        );
                        if (dist < 300 && dist < minDist) {
                            const text = (btn.textContent || '').trim().toLowerCase();
                            const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                            const titleAttr = (btn.getAttribute('title') || '').toLowerCase();
                            const className = (btn.className || '').toString().toLowerCase();
                            if (
                                aria.includes('favorite') || aria.includes('wishlist') || aria.includes('collect') || aria.includes('like') || aria.includes('heart') ||
                                titleAttr.includes('favorite') || titleAttr.includes('wishlist') || titleAttr.includes('collect') || titleAttr.includes('like') || titleAttr.includes('heart') ||
                                text.includes('favorite') || text.includes('wishlist') || text.includes('collect') || text.includes('like') || text.includes('heart') ||
                                className.includes('favorite') || className.includes('wishlist') || className.includes('collect') || className.includes('like') || className.includes('heart') || className.includes('fav-') || className.includes('-fav')
                            ) {
                                minDist = dist;
                                bestBtn = btn;
                            }
                        }
                    }
                    if (bestBtn) favBtn = bestBtn;
                }

                if (!favBtn) {
                    results.push({index: idx, status: 'no_button', cardText: title});
                    continue;
                }

                favBtn.scrollIntoView({block: 'center'});
                favBtn.click();
                results.push({index: idx, status: 'clicked', cardText: title});
            }

            return JSON.stringify({total: cards.length, results});
        }
        """, indices)

        data = json.loads(result_raw)

        if 'error' in data:
            return f"Failure: {data['error']}"

        results = data.get('results', [])
        if not results:
            return f"No cards added to favorites for '{keyword}'."

        added = [r for r in results if r['status'] == 'clicked']
        no_btn = [r for r in results if r['status'] == 'no_button']

        if added:
            await asyncio.sleep(2.0)

        summary_parts = []
        if added:
            summary_parts.append(f"Added {len(added)} item(s) to favorites")
        if no_btn:
            summary_parts.append(f"{len(no_btn)} had no favorites button")

        detail_lines = []
        for r in results:
            status_label = {'clicked': '✅ ADDED TO FAVORITES', 'no_button': '➖ NO FAVORITES BUTTON', 'out_of_bounds': '⚠️ INVALID INDEX'}
            detail_lines.append(f"  Card {r['index']}: {status_label.get(r['status'], r['status'])} - {r.get('cardText', '')}")

        summary = f"Summary: {', '.join(summary_parts)} (for '{keyword}')."
        logger.info(f"[weee_add_to_favorites_by_indices] {summary}")
        return summary + "\n" + "\n".join(detail_lines)

    except Exception as e:
        logger.error(f"[weee_add_to_favorites_by_indices] Error: {e}")
        return f"Failure: Error in weee_add_to_favorites_by_indices: {str(e)}"


async def weee_get_all_deals(browser: BrowserSession, keyword: str = ""):
    """SITE-SPECIFIC SKILL: Scrapes ALL visible product cards on the current page.
    Optionally filters by a keyword.
    """
    page = await browser.get_current_page()
    try:
        logger.info(f"[weee_get_all_deals] Scraping all deals. Keyword filter: '{keyword}'")
        await asyncio.sleep(1.5)

        raw = await page.evaluate("""
        (kw) => {
            const cards = Array.from(document.querySelectorAll('a[aria-label^="weee "]')).filter(el => el.offsetParent !== null);
            if (cards.length === 0) {
                return JSON.stringify({error: 'No product cards found on the page.'});
            }

            const kwLower = (kw || '').toLowerCase();
            const deals = [];
            const seen = new Set();

            for (let i = 0; i < cards.length; i++) {
                const card = cards[i];
                const label = card.getAttribute('aria-label') || '';
                let title = label.substring(5).trim();
                const words = title.split(' ');
                let productId = '';
                if (words.length > 0 && /^\d+$/.test(words[words.length - 1])) {
                    productId = words[words.length - 1];
                    words.pop();
                    title = words.join(' ');
                }

                // Filter by keyword if provided
                if (kwLower && title.toLowerCase().indexOf(kwLower) === -1) continue;

                // Extract prices
                let salePrice = '';
                let originalPrice = '';
                let unitPrice = '';
                let discount = '';

                const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
                const dollarTexts = [];
                const allTexts = [];
                while (walker.nextNode()) {
                    const txt = walker.currentNode.textContent.trim();
                    if (!txt) continue;
                    allTexts.push(txt);
                    if (txt.startsWith('$')) {
                        if (txt.includes('/')) {
                            unitPrice = txt;
                        } else {
                            dollarTexts.push(txt);
                        }
                    } else if (/\d+%\s*off/i.test(txt)) {
                        discount = txt;
                    }
                }

                if (dollarTexts.length > 0) salePrice = dollarTexts[0];
                if (dollarTexts.length > 1) originalPrice = dollarTexts[1];

                // Fallback for prices inside the parent container of the card
                if (!salePrice) {
                    let parent = card.parentElement;
                    for (let depth = 0; depth < 3; depth++) {
                        if (!parent) break;
                        const texts = [];
                        const pWalker = document.createTreeWalker(parent, NodeFilter.SHOW_TEXT);
                        while (pWalker.nextNode()) {
                            const txt = pWalker.currentNode.textContent.trim();
                            if (txt.startsWith('$') && !txt.includes('/') && !texts.includes(txt)) {
                                texts.push(txt);
                            }
                        }
                        if (texts.length > 0) salePrice = texts[0];
                        if (texts.length > 1) originalPrice = texts[1];
                        if (salePrice) break;
                        parent = parent.parentElement;
                    }
                }

                // Extract weight
                let weight = '';
                const weightPattern = /\\b\\d+(?:\\.\\d+)?\\s*(?:lb|oz|g|kg|ml|l|fl\\.?\\s*oz|ct|pack|pcs|count|bag|bunch|ea|each)\\b/i;
                for (const txt of allTexts) {
                    if (weightPattern.test(txt) && !txt.startsWith('$') && txt.toLowerCase() !== title.toLowerCase()) {
                        weight = txt;
                        break;
                    }
                }

                if (!weight) {
                    const match = title.match(weightPattern);
                    if (match) {
                        weight = match[0];
                    }
                }

                const link = card.href || '';

                let cardText = `Title: ${title} | Weight: ${weight || 'N/A'} | Price: ${originalPrice || 'N/A'} | Deal Price: ${salePrice || 'N/A'} | Link: ${link}`;
                if (discount) cardText += ` | Discount: ${discount}`;

                if (!seen.has(cardText)) {
                    seen.add(cardText);
                    deals.push('Card ' + i + ': ' + cardText);
                    if (deals.length >= 60) break;
                }
            }

            return JSON.stringify({total: cards.length, deals: deals});
        }
        """, keyword)

        data = json.loads(raw)

        if 'error' in data:
            return f"Failure: {data['error']}"

        deals = data.get('deals', [])
        total = data.get('total', 0)

        if not deals:
            logger.warning(f"[weee_get_all_deals] No deals matching '{keyword}' found among {total} cards.")
            return f"No deals matching '{keyword}' found among {total} visible cards."

        logger.info(f"[weee_get_all_deals] Successfully found {len(deals)} deals matching keyword out of {total} total cards.")
        return "Found deals:\n" + "\n".join(deals)

    except Exception as e:
        return f"Failure: Error in weee_get_all_deals: {str(e)}"


async def weee_filter_category(category_name: str, browser: BrowserSession):
    """SITE-SPECIFIC SKILL: Filters the product list on Weee by selecting a category."""
    page = await browser.get_current_page()
    try:
        logger.info(f"[weee_filter_category] Filtering by category: '{category_name}'")
        await asyncio.sleep(1.5)

        click_result = await page.evaluate("""
        (targetText) => {
            const sidebar = document.querySelector('div[aria-label="Result List"]') || document;
            const targetLower = targetText.toLowerCase();
            const elements = Array.from(sidebar.querySelectorAll('a, button, [role="link"], [role="button"]'));
            
            let matchedEl = null;
            
            // 1. Exact match on aria-label or text
            for (const el of elements) {
                const label = (el.getAttribute('aria-label') || '').toLowerCase();
                const text = (el.textContent || el.innerText || '').toLowerCase();
                if (label === targetLower || text === targetLower) {
                    matchedEl = el;
                    break;
                }
            }
            
            // 2. Fuzzy match
            if (!matchedEl) {
                for (const el of elements) {
                    const label = (el.getAttribute('aria-label') || '').toLowerCase();
                    const text = (el.textContent || el.innerText || '').toLowerCase();
                    if (label.includes(targetLower) || text.includes(targetLower)) {
                        matchedEl = el;
                        break;
                    }
                }
            }
            
            if (!matchedEl) return JSON.stringify({error: 'Category element not found'});
            
            matchedEl.scrollIntoView({block: 'center', behavior: 'instant'});
            matchedEl.click();
            return JSON.stringify({success: true, text: matchedEl.getAttribute('aria-label') || matchedEl.innerText});
        }
        """, category_name)

        data = json.loads(click_result)
        if 'success' in data:
            logger.info(f"[weee_filter_category] Successfully filtered category to '{data['text']}'")
            await asyncio.sleep(3.0)
            return f"Success: Filtered category to '{data['text']}'"
        else:
            return f"Failure: {data['error']}"
    except Exception as e:
        return f"Failure: Error in weee_filter_category: {str(e)}"


async def weee_get_categories(browser: BrowserSession):
    """SITE-SPECIFIC SKILL: Scrapes all category filters from Weee."""
    page = await browser.get_current_page()
    try:
        logger.info("[weee_get_categories] Waiting for category list to load...")
        await asyncio.sleep(2.5)

        categories_raw = await page.evaluate("""
        () => {
            const sidebar = document.querySelector('div[aria-label="Result List"]') || document;
            const categories = [];
            
            const elements = sidebar.querySelectorAll('a, button, [role="link"], [role="button"]');
            elements.forEach(el => {
                const label = el.getAttribute('aria-label') || '';
                const text = (el.innerText || el.textContent || '').trim();
                
                let categoryName = label || text;
                categoryName = categoryName.trim();
                
                if (categoryName && categoryName.length > 1 && categoryName.length < 30 && !categories.includes(categoryName)) {
                    if (!/clear|all|apply|filter|sort|sale/i.test(categoryName)) {
                        categories.push(categoryName);
                    }
                }
            });
            
            return JSON.stringify(categories);
        }
        """)
        categories = json.loads(categories_raw)
        logger.info(f"[weee_get_categories] Found {len(categories)} categories: {categories}")
        return categories
    except Exception as e:
        logger.error(f"[weee_get_categories] Failed: {e}")
        return []


async def weee_run_pre_flight(browser: BrowserSession, prompt: str, context_str: str, log_path: str, llm: Any):
    """SITE-SPECIFIC AUTOMATION: Pre-flight deal finding and cart addition for Weee."""
    import asyncio as _asyncio
    import re
    import json
    import httpx
    import config
    
    try:
        target_url = "https://www.sayweee.com/en/on-sale"
        if context_str:
            url_match = re.search(r'https?://[^\s)]+', context_str)
            if url_match:
                target_url = url_match.group(0).rstrip('.')
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- PRE-FLIGHT (Weee): Navigating to {target_url} ---\n")
            
        page = await browser.get_current_page()
        await page.goto(target_url)
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await _asyncio.sleep(5)
        
        # Auto-close modal if visible
        try:
            await page.evaluate("""
            () => {
                const closeBtn = document.querySelector('button[class*="absolute"][class*="right-4"][class*="top-4"], button[class*="close"], [aria-label*="close" i]');
                if (closeBtn) {
                    closeBtn.click();
                    return true;
                }
                return false;
            }
            """)
            await _asyncio.sleep(1.0)
        except Exception as me:
            logger.warning(f"[weee_run_pre_flight] Modal close button error (likely not present): {me}")

        # Extract items
        extraction_system = (
            "You are a product extraction assistant. Your job is to extract a list of specific products or items from a user's prompt.\n"
            "Output ONLY a valid JSON object with the key 'items' containing a list of strings.\n"
            "Example prompt: 'Get deals for milk, ice cream, and steak'\n"
            "Output: {\"items\": [\"milk\", \"ice cream\", \"steak\"]}\n"
            "If no specific items are found, output {\"items\": []}."
        )
        items = []
        try:
            async with httpx.AsyncClient() as client:
                extraction_resp = await client.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": config.LLM_MODEL,
                        "messages": [{"role": "user", "content": f"{extraction_system}\n\nPrompt: {prompt}"}],
                        "stream": False,
                        "format": "json",
                        "think": False,
                        "options": {"temperature": 0, "num_ctx": 4096}
                    },
                    timeout=config.LLM_TIMEOUT
                )
                raw_content = extraction_resp.json().get("message", {}).get("content", "{}")
                data = json.loads(raw_content)
                items = data.get("items", [])
                logger.info(f"[weee_run_pre_flight] Extracted items from prompt: {items}")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Extracted items from prompt: {items}\n")
        except Exception as e:
            logger.error(f"[weee_run_pre_flight] Keyword extraction failed: {e}. Falling back to simple extraction.")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT: Keyword extraction failed: {e}. Falling back to simple extraction.\n")
            noise_words = {"look", "for", "deals", "weee", "sayweee", "website", "following", "item:", "items:", "item", "items", "search", "find", "on", "products", "product", "the", "a", "an"}
            items = [" ".join([w for w in prompt.lower().split() if w not in noise_words and len(w) > 2]).strip()]

        if not items:
            items = [prompt[:30]]
            
        logger.info(f"[weee_run_pre_flight] Final list of items to process: {items}")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT: Final list of items to process: {items}\n")

        # Categorize items
        available_categories = await weee_get_categories(browser)
        item_category_map = {}
        
        if available_categories and items:
            selector_system = (
                "You are a categorization assistant for a grocery store. You must output ONLY a valid JSON object.\n"
                "Format: {\"mapping\": [{\"item\": \"item name\", \"category\": \"Category Name\"}, ...]}\n"
                "RULES: 1. NEVER pick 'Special Offers' if a specific food category is available. 2. If nothing fits, use 'NONE' as category.\n"
                f"Available Categories: {', '.join(available_categories)}"
            )
            try:
                async with httpx.AsyncClient() as client:
                    selector_resp = await client.post(
                        "http://localhost:11434/api/chat",
                        json={
                            "model": config.LLM_MODEL,
                            "messages": [{"role": "user", "content": f"{selector_system}\n\nItems: {', '.join(items)}"}],
                            "stream": False,
                            "format": "json",
                            "think": False,
                            "options": {"temperature": 0, "num_ctx": 4096}
                        },
                        timeout=config.LLM_TIMEOUT
                    )
                    raw_content = selector_resp.json().get("message", {}).get("content", "{}")
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: Categorization response: {raw_content}\n")
                    
                    data = json.loads(raw_content)
                    mappings = data.get("mapping", [])
                    
                    logger.info(f"[weee_run_pre_flight] Item Categorization Breakdown:")
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
                logger.error(f"[weee_run_pre_flight] Multi-Category mapping failed: {type(e).__name__}: {e}")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Multi-Category mapping failed: {type(e).__name__}: {e}\n")

        if not item_category_map:
            for item in items:
                if "" not in item_category_map:
                    item_category_map[""] = []
                item_category_map[""].append(item)

        # Scrape and add to cart
        item_results = []
        for category, cat_items in item_category_map.items():
            if category:
                with open(log_path, "a", encoding="utf-8") as f: 
                    f.write(f"PRE-FLIGHT: Applying category filter '{category}' for items: {cat_items}...\n")
                await weee_filter_category(category, browser)
                await _asyncio.sleep(3)
            
            keywords_to_search = cat_items
            for kw in keywords_to_search:
                result_entry = {"item": kw, "category": category or "General", "clipped": [], "already": [], "no_button": [], "no_match": False, "llm_error": False}
                
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Scraping deals for '{kw}' in '{category or 'General'}'...\n")
                scrape_result = await weee_get_all_deals(browser, keyword=kw)
                
                found_deals = False
                if scrape_result.startswith("Found deals:"):
                    found_deals = True
                else:
                    scrape_result = await weee_get_all_deals(browser, keyword="")
                    if scrape_result.startswith("Found deals:"):
                        found_deals = True

                if found_deals:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT: LLM-matching deals for '{kw}' in '{category or 'General'}'...\n")
                    
                    card_texts_raw = await page.evaluate("""
                    () => {
                        const cards = Array.from(document.querySelectorAll('a[aria-label^="weee "]')).filter(el => el.offsetParent !== null);
                        return JSON.stringify(cards.map(c => {
                            const label = c.getAttribute('aria-label') || '';
                            let title = label.substring(5).trim();
                            const words = title.split(' ');
                            if (words.length > 0 && /^\d+$/.test(words[words.length - 1])) {
                                words.pop();
                                title = words.join(' ');
                            }

                            let salePrice = '';
                            let originalPrice = '';
                            let unitPrice = '';
                            let discount = '';

                            const walker = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
                            const dollarTexts = [];
                            const allTexts = [];
                            while (walker.nextNode()) {
                                const txt = walker.currentNode.textContent.trim();
                                if (!txt) continue;
                                allTexts.push(txt);
                                if (txt.startsWith('$')) {
                                    if (txt.includes('/')) {
                                        unitPrice = txt;
                                    } else {
                                        dollarTexts.push(txt);
                                    }
                                } else if (/\d+%\s*off/i.test(txt)) {
                                    discount = txt;
                                }
                            }

                            if (dollarTexts.length > 0) salePrice = dollarTexts[0];
                            if (dollarTexts.length > 1) originalPrice = dollarTexts[1];

                            if (!salePrice) {
                                let parent = c.parentElement;
                                for (let depth = 0; depth < 3; depth++) {
                                    if (!parent) break;
                                    const texts = [];
                                    const pWalker = document.createTreeWalker(parent, NodeFilter.SHOW_TEXT);
                                    while (pWalker.nextNode()) {
                                        const txt = pWalker.currentNode.textContent.trim();
                                        if (txt.startsWith('$') && !txt.includes('/') && !texts.includes(txt)) {
                                            texts.push(txt);
                                        }
                                    }
                                    if (texts.length > 0) salePrice = texts[0];
                                    if (texts.length > 1) originalPrice = texts[1];
                                    if (salePrice) break;
                                    parent = parent.parentElement;
                                }
                            }

                            // Extract weight
                            let weight = '';
                            const weightPattern = /\\b\\d+(?:\\.\\d+)?\\s*(?:lb|oz|g|kg|ml|l|fl\\.?\\s*oz|ct|pack|pcs|count|bag|bunch|ea|each)\\b/i;
                            for (const txt of allTexts) {
                                if (weightPattern.test(txt) && !txt.startsWith('$') && txt.toLowerCase() !== title.toLowerCase()) {
                                    weight = txt;
                                    break;
                                }
                            }

                            if (!weight) {
                                const match = title.match(weightPattern);
                                if (match) {
                                    weight = match[0];
                                }
                            }

                            const link = c.href || '';

                            let cardText = `Title: ${title} | Weight: ${weight || 'N/A'} | Price: ${originalPrice || 'N/A'} | Deal Price: ${salePrice || 'N/A'} | Link: ${link}`;
                            if (discount) cardText += ` | Discount: ${discount}`;
                            return cardText;
                        }));
                    }
                    """)
                    card_texts = json.loads(card_texts_raw)
                    
                    matching_indices = await _llm_match_cards(card_texts, kw, log_path)
                    
                    if matching_indices is None:
                        result_entry["llm_error"] = True
                        add_result = f"LLM ERROR: Model failed while matching '{kw}' among {len(card_texts)} cards."
                    elif matching_indices:
                        seen_texts = {}
                        unique_indices = []
                        for idx in matching_indices:
                            if idx < len(card_texts):
                                dedup_key = card_texts[idx].strip().lower()
                                if dedup_key not in seen_texts:
                                    seen_texts[dedup_key] = idx
                                    unique_indices.append(idx)
                        
                        add_result = await weee_add_to_favorites_by_indices(browser, unique_indices, keyword=kw)
                        
                        seen_descs = set()
                        for line in add_result.splitlines():
                            line_stripped = line.strip()
                            if "✅ ADDED TO FAVORITES" in line_stripped:
                                title_parsed = line_stripped.split("ADDED TO FAVORITES - ", 1)[-1] if "ADDED TO FAVORITES - " in line_stripped else line_stripped
                                matched_desc = next((ct for ct in card_texts if title_parsed.lower() in ct.lower()), title_parsed)
                                desc_key = matched_desc[:100].lower()
                                if desc_key not in seen_descs:
                                    seen_descs.add(desc_key)
                                    result_entry["clipped"].append(matched_desc)
                            elif "➖ NO FAVORITES BUTTON" in line_stripped:
                                title_parsed = line_stripped.split("NO FAVORITES BUTTON - ", 1)[-1] if "NO FAVORITES BUTTON - " in line_stripped else line_stripped
                                matched_desc = next((ct for ct in card_texts if title_parsed.lower() in ct.lower()), title_parsed)
                                desc_key = matched_desc[:100].lower()
                                if desc_key not in seen_descs:
                                    seen_descs.add(desc_key)
                                    result_entry["no_button"].append(matched_desc)
                    else:
                        result_entry["no_match"] = True
                        add_result = f"LLM found no semantic matches for '{kw}' among {len(card_texts)} cards."
                    
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT ADD TO FAVORITES RESULT: {add_result}\n")
                    logger.info(f"[weee_run_pre_flight] Add result for '{kw}': {add_result.splitlines()[0] if add_result else 'empty'}")
                else:
                    result_entry["no_match"] = True
                
                item_results.append(result_entry)

        if not item_results:
            return "No deals found for the requested items."
        
        from collections import defaultdict
        category_groups = defaultdict(list)
        for r in item_results:
            cat = r["category"] or "General Deals"
            category_groups[cat].append(r)
            
        summary_lines = ["# 💖 Weee Favorite Results"]
        total_added = 0
        total_no_button = 0
        not_found_items = []
        error_items = []
        
        for category, results in category_groups.items():
            category_has_results = False
            for r in results:
                has_results = r["clipped"] or r["no_button"]
                if r.get("llm_error") and not has_results:
                    error_items.append(r["item"])
                elif not has_results and r["no_match"]:
                    not_found_items.append((r["item"], r["category"]))
                else:
                    category_has_results = True
            
            if not category_has_results:
                continue
                
            emoji = CATEGORY_EMOJIS.get(category, "📦")
            summary_lines.append(f"\n### {emoji} {category}")
            
            for r in results:
                has_results = r["clipped"] or r["no_button"]
                if not has_results:
                    continue
                
                summary_lines.append(f"\n#### **{r['item'].upper()}**")
                
                if r["clipped"]:
                    total_added += len(r["clipped"])
                    summary_lines.append(f"* **✅ Added to Favorites {len(r['clipped'])} item(s):**")
                    for i, desc in enumerate(r["clipped"], 1):
                        formatted_desc = _format_deal_markdown(desc)
                        summary_lines.append(f"  {i}. {formatted_desc}")
                if r["no_button"]:
                    total_no_button += len(r["no_button"])
                    summary_lines.append(f"* **➖ No Favorites Button Found: {len(r['no_button'])}**")
                    for i, desc in enumerate(r["no_button"], 1):
                        formatted_desc = _format_deal_markdown(desc)
                        summary_lines.append(f"  {i}. {formatted_desc}")
        
        if not_found_items:
            summary_lines.append("\n### ❌ No Coupons Found")
            for item, cat in not_found_items:
                summary_lines.append(f"- **{item.upper()}** (searched in {cat})")
        
        if error_items:
            summary_lines.append("\n### ⚠️ LLM Errors")
            for item in error_items:
                summary_lines.append(f"- **{item.upper()}** (Model failed, could not search)")
        
        summary_lines.append("\n***")
        summary_lines.append(f"📊 **SUMMARY:** {len(item_results)} items searched | {total_added} added to favorites | {total_no_button} no favorites button | {len(not_found_items)} not found | {len(error_items)} errors")
        
        if error_items and total_added == 0 and total_no_button == 0 and not not_found_items:
            logger.error(f"[weee_run_pre_flight] ALL items failed due to LLM errors. Signaling task failure.")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT FATAL: All {len(error_items)} items failed due to LLM errors. Task will be marked FAILED.\n")
            return ""
        
        return "\n".join(summary_lines)

    except Exception as e:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT ERROR: {e}\n")
        return ""

CATEGORY_EMOJIS = {
    "Beverages": "🥤",
    "Dairy, Eggs & Cheese": "🥛",
    "Dairy": "🥛",
    "Meat & Seafood": "🥩",
    "Fruits & Vegetables": "🍎",
    "Vegetables": "🥬",
    "Fruits": "🍇",
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

class WeeePlugin(BaseSitePlugin):
    async def run_pre_flight(self, browser, prompt: str, context: str, log_path: str, llm) -> str:
        return await weee_run_pre_flight(browser, prompt, context, log_path, llm)
