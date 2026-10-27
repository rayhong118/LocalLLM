# site_skills/slickdeals.py
"""
Slickdeals RSS Automation Plugin.
Monitors watchlisted items via targeted keyword RSS feeds and/or tracks hot Frontpage and Popular deals.
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

import httpx

from backend import config
from backend.core.plugin import BaseSitePlugin
from backend.services.llm import clean_json_text

logger = logging.getLogger(__name__)

# ==============================================================================
# Endpoints & Constants
# ==============================================================================

BASE_SEARCH_RSS = "https://slickdeals.net/newsearch.php"
FRONTPAGE_RSS_URL = "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1&hideexpired=1"
POPULAR_RSS_URL = "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1&hideexpired=1"
MAX_DEAL_AGE_DAYS = 14  # Exclude deals posted more than 14 days ago

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Common noise words to ignore when extracting watchlist terms from prompt
PROMPT_NOISE_WORDS = {
    "slickdeals", "slickdeal", "deals", "deal", "check", "find", "search", 
    "for", "on", "the", "and", "or", "in", "item", "items", "watch", "watchlist", 
    "please", "monitor", "look", "get", "show", "list", "me", "latest", "hot", "popular"
}


def _log(log_path: str, msg: str) -> None:
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
    logger.info(msg)


# ==============================================================================
# Watchlist Extraction
# ==============================================================================

def extract_watchlist_items(prompt: str, context_str: str = "", log_path: str = "") -> List[str]:
    """
    Extract target items to search from bound context or prompt.
    Returns a deduplicated list of cleaned item search strings.
    """
    items: List[str] = []

    # 1. First priority: Bound context string
    if context_str:
        # Check blocks formatted like --- Context Name --- \n items
        lines = [line.strip() for line in context_str.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("---") or line.startswith("PRIOR KNOWLEDGE") or line.startswith("USE THIS"):
                continue
            # Split comma-separated or newline-separated items
            for token in re.split(r'[,;\n]+', line):
                clean = re.sub(r'^[•\-\*\d\.\)\s]+', '', token).strip()
                if clean and len(clean) >= 2 and clean.lower() not in PROMPT_NOISE_WORDS:
                    if clean not in items:
                        items.append(clean)

    # 2. Second priority: Query active database contexts if no items from bound context
    if not items:
        try:
            from backend.database.session import SessionLocal
            from backend.database.models import Context
            with SessionLocal() as db:
                contexts = db.query(Context).all()
                deal_kws = ["slickdeals", "deals", "deal", "shopping", "products", "items", "electronics", "gadgets"]
                grocery_stock_kws = ["safeway", "weee", "grocery", "groceries", "stock", "portfolio", "ticker"]
                for c in contexts:
                    name_lower = c.name.lower()
                    if any(bad in name_lower for bad in grocery_stock_kws):
                        continue
                    if any(good in name_lower for good in deal_kws) or "watchlist" in name_lower:
                        for token in re.split(r'[,;\n]+', c.content):
                            clean = re.sub(r'^[•\-\*\d\.\)\s]+', '', token).strip()
                            if clean and len(clean) >= 2 and clean.lower() not in PROMPT_NOISE_WORDS:
                                if clean not in items:
                                    items.append(clean)
        except Exception as e:
            _log(log_path, f"Slickdeals: Database context query note: {e}")

    # 3. Third priority: Extract items from prompt
    # E.g.: "check slickdeals for ipad, airpods, oled tv"
    if not items:
        # Match pattern: for/on/search <items>
        match = re.search(r'(?:for|deals\s+on|search\s+for|search|watch)\s+([A-Za-z0-9\s,\-\/\+]+?)(?:\s+(?:on|using|via)\s+slickdeals|\s*$)', prompt, re.IGNORECASE)
        if match:
            candidate_text = match.group(1).strip()
            # Split by comma or 'and'
            parts = re.split(r'[,]+|\band\b', candidate_text, flags=re.IGNORECASE)
            for part in parts:
                clean = part.strip()
                words = [w for w in clean.split() if w.lower() not in PROMPT_NOISE_WORDS]
                candidate = " ".join(words).strip()
                if candidate and len(candidate) >= 2 and candidate.lower() not in PROMPT_NOISE_WORDS:
                    if candidate not in items:
                        items.append(candidate)

    if items and log_path:
        _log(log_path, f"Slickdeals: Resolved {len(items)} watchlist item(s): {', '.join(items)}")

    return items


def is_hot_popular_request(prompt: str) -> bool:
    """Return True if prompt explicitly asks for trending, hot, popular, or frontpage deals."""
    p_lower = prompt.lower()
    return any(kw in p_lower for kw in ["hot deal", "popular", "frontpage", "trending", "top deal", "best deal", "today's deal"])


# ==============================================================================
# RSS Fetching & Parsing
# ==============================================================================

def clean_deal_title(raw_title: str) -> str:
    """Unescape HTML and strip excess whitespace from deal titles."""
    return html.unescape(raw_title or "").strip()


def parse_clean_link(raw_link: str) -> str:
    """Strip tracking UTM query parameters from deal links for clean output."""
    if not raw_link:
        return ""
    clean = raw_link.split("?")[0].strip()
    return clean or raw_link.strip()


def extract_price_and_store(title: str, description: str) -> Tuple[str, str]:
    """
    Extract price and store/merchant name from deal title and description.
    Returns empty string for price if no verified price ($XX, from $XX, Free) is found.
    """
    price = ""

    # Filter out order threshold mentions (e.g. "on $50+ orders", "spend $50") so they aren't confused with item price
    filtered_title = re.sub(
        r'\b(?:on|for|with|after)\s+\$[\d,]+(?:\.\d{2})?\+?\s+(?:orders?|purchases?|min(?:imum)?|cart)\b',
        ' ',
        title,
        flags=re.IGNORECASE
    )
    filtered_title = re.sub(
        r'\bspend\s+\$[\d,]+(?:\.\d{2})?',
        ' ',
        filtered_title,
        flags=re.IGNORECASE
    )

    # 1. Price extraction from title (e.g. $49.99, $1,299, from $15, Free)
    price_match = re.search(r'(?:from\s+)?\$[\d,]+(?:\.\d{2})?', filtered_title, re.IGNORECASE)
    if price_match:
        price = price_match.group(0).strip()
    elif re.search(r'\bfree\b', filtered_title, re.IGNORECASE):
        # Exclude cases where "free" refers only to shipping, delivery, trial, or returns
        if not re.search(r'\bfree\s+(?:shipping|delivery|pickup|returns?|trial|member(?:ship)?)\b', filtered_title, re.IGNORECASE):
            price = "Free"
        elif re.search(r'\b(?:100%\s+free|completely\s+free|free\s+after\s+rebate)\b', filtered_title, re.IGNORECASE):
            price = "Free"

    # 2. Fallback check in description if title doesn't specify price
    if not price and description:
        filtered_desc = re.sub(
            r'\b(?:on|for|with|after)\s+\$[\d,]+(?:\.\d{2})?\+?\s+(?:orders?|purchases?|min(?:imum)?|cart)\b',
            ' ',
            description,
            flags=re.IGNORECASE
        )
        filtered_desc = re.sub(
            r'\bspend\s+\$[\d,]+(?:\.\d{2})?',
            ' ',
            filtered_desc,
            flags=re.IGNORECASE
        )
        desc_price_match = re.search(r'(?:from\s+)?\$[\d,]+(?:\.\d{2})?', filtered_desc, re.IGNORECASE)
        if desc_price_match:
            price = desc_price_match.group(0).strip()
        elif re.search(r'\bfree\b', filtered_desc, re.IGNORECASE):
            if not re.search(r'\bfree\s+(?:shipping|delivery|pickup|returns?|trial|member(?:ship)?)\b', filtered_desc, re.IGNORECASE):
                price = "Free"
            elif re.search(r'\b(?:100%\s+free|completely\s+free|free\s+after\s+rebate)\b', filtered_desc, re.IGNORECASE):
                price = "Free"

    # Store/merchant extraction (e.g. [amazon.com], via Best Buy, Costco Members:)
    store = ""
    store_bracket = re.search(r'\[([a-zA-Z0-9\.\-_]+)\]', description)
    if store_bracket:
        store = store_bracket.group(1).strip()
    else:
        store_prefix = re.search(r'^(?:via\s+|at\s+|from\s+)?([A-Za-z0-9\s]+?)(?:Members?:|\:|\s+has\s+|\s+via\s+)', title)
        if store_prefix:
            cand = store_prefix.group(1).strip()
            if len(cand) <= 25 and cand.lower() not in {"deal", "hot", "price", "open boxes", "refurb", "new"}:
                store = cand

    return price, store or "Online"


def parse_rss_items(
    xml_content: str,
    max_items: int = 25,
    max_age_days: Optional[int] = MAX_DEAL_AGE_DAYS
) -> List[Dict[str, Any]]:
    """Parse RSS XML content into structured deal objects, filtering out expired, stale, and priceless deals."""
    deals = []
    if not xml_content:
        return deals

    try:
        root = ET.fromstring(xml_content)
        channel = root.find("channel")
        if channel is None:
            return deals

        now_utc = datetime.now(timezone.utc)
        items = channel.findall("item")
        for item in items:
            if len(deals) >= max_items:
                break

            title = clean_deal_title(item.findtext("title") or "")
            link = parse_clean_link(item.findtext("link") or "")
            pub_date = item.findtext("pubDate") or ""
            raw_desc = html.unescape(item.findtext("description") or "")
            clean_desc = re.sub(r'<[^>]+>', ' ', raw_desc).strip()

            if not title or not link:
                continue

            # 1. Skip if title clearly indicates expired or dead deal
            if re.search(r'\b(?:expired|deal is dead|deal dead|ended)\b', title, re.IGNORECASE):
                continue

            # 2. Skip if older than max_age_days
            if pub_date and max_age_days:
                try:
                    dt = parsedate_to_datetime(pub_date)
                    age_days = (now_utc - dt).total_seconds() / 86400
                    if age_days > max_age_days:
                        continue
                except Exception:
                    pass

            price, store = extract_price_and_store(title, clean_desc)

            # 3. Only take deals that have valid price info
            if not price:
                continue

            deals.append({
                "title": title,
                "link": link,
                "price": price,
                "store": store,
                "pub_date": pub_date,
                "description": clean_desc[:250],
            })
    except Exception as e:
        logger.warning(f"Error parsing RSS XML: {e}")

    return deals


async def fetch_rss_feed(url: str, client: httpx.AsyncClient) -> str:
    """Fetch RSS content with timeout and error handling."""
    try:
        resp = await client.get(url, headers=DEFAULT_HEADERS, timeout=12.0, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        else:
            logger.warning(f"Slickdeals RSS returned HTTP {resp.status_code} for {url}")
            return ""
    except Exception as e:
        logger.warning(f"Error fetching RSS from {url}: {e}")
        return ""


# ==============================================================================
# Search & Matching Logic
# ==============================================================================

async def search_deals_for_item(
    item_query: str,
    client: httpx.AsyncClient,
    max_results: int = 5,
    max_age_days: Optional[int] = MAX_DEAL_AGE_DAYS,
    log_path: str = ""
) -> List[Dict[str, Any]]:
    """
    Search deals for a specific target item.
    First tries searchin=title for precision, then falls back to searchin=first if needed.
    Filters out expired threads via hideexpired=1 and recency threshold.
    """
    encoded_query = httpx.URL("", params={"q": item_query}).query.decode("utf-8")
    
    # 1. High precision title search
    title_url = f"{BASE_SEARCH_RSS}?{encoded_query}&searcharea=deals&searchin=title&rss=1&hideexpired=1"
    xml_data = await fetch_rss_feed(title_url, client)
    deals = parse_rss_items(xml_data, max_items=max_results, max_age_days=max_age_days)

    # 2. Fallback to broad first-post search if title search returned 0 items
    if not deals:
        first_url = f"{BASE_SEARCH_RSS}?{encoded_query}&searcharea=deals&searchin=first&rss=1&hideexpired=1"
        xml_data = await fetch_rss_feed(first_url, client)
        all_broad = parse_rss_items(xml_data, max_items=15, max_age_days=max_age_days)
        
        # Filter broad results so they actually mention the query keywords
        tokens = [t.lower() for t in item_query.split() if len(t) > 1]
        for d in all_broad:
            d_title_lower = d["title"].lower()
            if any(t in d_title_lower for t in tokens):
                deals.append(d)
                if len(deals) >= max_results:
                    break

    _log(log_path, f"Slickdeals: Found {len(deals)} active deal(s) for '{item_query}'")
    return deals


async def fetch_frontpage_deals(client: httpx.AsyncClient, limit: int = 25) -> List[Dict[str, Any]]:
    """Fetch current Frontpage curated deals."""
    xml_data = await fetch_rss_feed(FRONTPAGE_RSS_URL, client)
    return parse_rss_items(xml_data, max_items=limit)


async def fetch_popular_deals(client: httpx.AsyncClient, limit: int = 25) -> List[Dict[str, Any]]:
    """Fetch current community Popular deals."""
    xml_data = await fetch_rss_feed(POPULAR_RSS_URL, client)
    return parse_rss_items(xml_data, max_items=limit)


# ==============================================================================
# LLM 2-Stage Query Optimization & Deal Validation
# ==============================================================================

async def extract_search_terms_with_llm(
    watchlist_items: List[str],
    log_path: str = ""
) -> Dict[str, str]:
    """
    Stage 1: Extract minimal, high-recall search terms from detailed item names.
    Temporarily omits secondary details (specs, colors, fluff) so Slickdeals keyword search can match broadly.
    """
    if not watchlist_items:
        return {}

    prompt = f"""Extract concise search keywords (brand + core product or model, 2-4 words) for each item to query a deal forum.
Omit secondary details: technical specifications (watts, Wh, mAh, voltage), storage, color, bundles, conditions, and parenthetical constraints.

Items:
{json.dumps(watchlist_items, indent=2)}

Return ONLY a valid JSON object mapping each original item string to its search query.
Example format:
{{
  "original item name": "search term"
}}
"""
    try:
        async with httpx.AsyncClient(timeout=float(getattr(config, "LLM_TIMEOUT", 120))) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "temperature": 0
                }
            )
            if resp.status_code == 200:
                raw = (
                    resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    or resp.json().get("message", {}).get("content", "")
                )
                data = json.loads(clean_json_text(raw))
                if isinstance(data, dict):
                    resolved = {}
                    for item in watchlist_items:
                        term = data.get(item) or data.get(item.strip()) or item
                        resolved[item] = str(term).strip()
                        _log(log_path, f"Slickdeals: Stage 1 Search Term: '{item}' -> '{resolved[item]}'")
                    return resolved
    except Exception as e:
        _log(log_path, f"Slickdeals: Stage 1 LLM extraction fallback ({e}); using raw item names.")

    return {item: item for item in watchlist_items}


async def validate_candidate_deals_with_llm(
    target_item: str,
    candidates: List[Dict[str, Any]],
    log_path: str = ""
) -> List[Dict[str, Any]]:
    """
    Stage 2: Validate candidate deals against the original full item name and its details.
    Ensures model numbers match, rejects accessories/cables, and distinguishes specs from models.
    """
    if not candidates:
        return []

    # Ensure only candidate deals with valid price information are evaluated
    candidates = [d for d in candidates if d.get("price") and d.get("price") != "See deal"]
    if not candidates:
        return []

    deal_cards = [
        f"[{i}] Title: \"{d['title']}\" | Price: {d.get('price', '')} | Store: {d.get('store', '')}"
        for i, d in enumerate(candidates, 1)
    ]
    deals_text = "\n".join(deal_cards)

    prompt = f"""You are a strict product deal validation assistant.
Determine whether candidate deals match the target item: "{target_item}".

Candidate Deals:
{deals_text}

Rules:
1. Product vs. Accessory: The deal must be for the actual product, NOT an accessory, case, skin, cable, or replacement part.
2. Model & Generation: If the target specifies an exact model number or generation, candidates for different models in the same product line do NOT match. Technical specifications (e.g. wattage, capacity, dimensions) are NOT model numbers.
3. Constraints & Generics: If the target specifies constraints (e.g. max wattage, size), candidates must satisfy them. If the target is generic without a specific model number, any product matching the brand and category is valid.

Respond with ONLY a JSON object in this format:
{{
  "evaluations": [
    {{
      "deal_index": 1,
      "is_match": true,
      "reason": "Brief explanation"
    }}
  ]
}}
"""
    try:
        async with httpx.AsyncClient(timeout=float(getattr(config, "LLM_TIMEOUT", 120))) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "temperature": 0
                }
            )
            if resp.status_code == 200:
                raw = (
                    resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    or resp.json().get("message", {}).get("content", "")
                )
                data = json.loads(clean_json_text(raw))
                evals = data.get("evaluations", [])
                match_map = {
                    int(ev["deal_index"]): (ev.get("is_match", False), ev.get("reason", ""))
                    for ev in evals
                    if "deal_index" in ev
                }

                validated = []
                for i, deal in enumerate(candidates, 1):
                    is_match, reason = match_map.get(i, (True, "No explicit evaluation"))
                    if is_match:
                        validated.append(deal)
                    else:
                        _log(log_path, f"Slickdeals: Stage 2 REJECTED deal for '{target_item}': '{deal['title']}' ({reason})")

                _log(log_path, f"Slickdeals: Stage 2 Validation for '{target_item}': {len(candidates)} candidate(s) -> {len(validated)} verified.")
                return validated
    except Exception as e:
        _log(log_path, f"Slickdeals: Stage 2 validation fallback due to {e}; retaining candidates.")
        return candidates

    return candidates


# ==============================================================================
# Markdown Report Formatting
# ==============================================================================

def format_deals_report(
    target_results: Dict[str, List[Dict[str, Any]]],
    frontpage_matches: List[Dict[str, Any]],
    curated_deals: Optional[List[Dict[str, Any]]] = None,
    is_general_feed: bool = False
) -> str:
    """Format the results into a markdown summary adhering to 4-tier evaluation format."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if is_general_feed:
        deals = curated_deals or []
        if not deals:
            return (
                f"## 🏷️ Slickdeals Top Deals Report ({now_str})\n\n"
                f"**Summary**: 0 items searched | 0 deals found | 0 not found\n\n"
                f"No active frontpage or popular deals were retrieved from Slickdeals."
            )

        lines = [
            f"## 🏷️ Slickdeals Hot & Popular Deals ({now_str})\n",
            f"**Summary**: {len(deals)} items searched | {len(deals)} deals found | 0 not found\n",
            f"Found **{len(deals)}** trending deals across Slickdeals Frontpage & Popular feeds:\n"
        ]

        for i, deal in enumerate(deals[:20], 1):
            lines.append(
                f"{i}. **[{deal['title']}]({deal['link']})**\n"
                f"   - **Price**: `{deal['price']}` | **Store**: *{deal['store']}*\n"
            )

        return "\n".join(lines)

    # Watchlist report
    total_searched = len(target_results)
    found_count = sum(1 for items in target_results.values() if items)
    not_found_count = total_searched - found_count

    lines = [
        f"## 🏷️ Slickdeals Watchlist Report ({now_str})\n",
        f"**Summary**: {total_searched} items searched | {found_count} deals found | {not_found_count} not found\n"
    ]

    # Highlight any frontpage matches
    if frontpage_matches:
        lines.append(f"### 🔥 Hot Frontpage Matches ({len(frontpage_matches)})\n")
        for match in frontpage_matches:
            lines.append(
                f"- **[{match['title']}]({match['link']})**\n"
                f"  - **Price**: `{match['price']}` | **Store**: *{match['store']}*\n"
            )
        lines.append("")

    # Detailed item results
    lines.append("### 📋 Watchlisted Items\n")
    for item_name, deals in target_results.items():
        if deals:
            lines.append(f"#### 🎯 {item_name} ({len(deals)} deal{'s' if len(deals) != 1 else ''} found)\n")
            for deal in deals[:3]:
                lines.append(
                    f"- **[{deal['title']}]({deal['link']})**\n"
                    f"  - **Price**: `{deal['price']}` | **Store**: *{deal['store']}*\n"
                )
        else:
            lines.append(f"#### ⚪ {item_name}: No active deals found on Slickdeals\n")

    return "\n".join(lines)


# ==============================================================================
# Main Plugin Class
# ==============================================================================

class SlickdealsPlugin(BaseSitePlugin):
    """
    Slickdeals RSS deal monitoring automation plugin.
    Fast, deterministic deal discovery using official RSS feeds for watchlists or trending bargains.
    """
    description: str = (
        "Search and monitor deals on Slickdeals using RSS feeds for watchlisted items or trending/popular deals."
    )

    async def run_pre_flight(
        self,
        browser: Any,
        prompt: str,
        context: str,
        log_path: str,
        llm: Any
    ) -> str:
        """
        Executes Slickdeals RSS retrieval.
        Runs purely over HTTP via RSS feeds without needing browser rendering overhead.
        """
        _log(log_path, "\n=== SLICKDEALS RSS AUTOMATION STARTED ===")
        start_time = datetime.now()

        # 1. Resolve watchlisted items
        watchlist_items = extract_watchlist_items(prompt, context, log_path)
        general_mode = is_hot_popular_request(prompt) and not watchlist_items

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Mode A: General Hot / Popular Deals
            if general_mode or (not watchlist_items and is_hot_popular_request(prompt)):
                _log(log_path, "Slickdeals: Executing Hot & Popular Deals Discovery mode...")
                fp_task = fetch_frontpage_deals(client, limit=20)
                pop_task = fetch_popular_deals(client, limit=20)
                fp_deals, pop_deals = await asyncio.gather(fp_task, pop_task)

                # Deduplicate by link
                seen_links: Set[str] = set()
                combined: List[Dict[str, Any]] = []
                for d in fp_deals + pop_deals:
                    if d["link"] not in seen_links:
                        seen_links.add(d["link"])
                        combined.append(d)

                elapsed = (datetime.now() - start_time).total_seconds()
                _log(log_path, f"Slickdeals: Finished in {elapsed:.2f}s with {len(combined)} deals.")
                return format_deals_report({}, [], curated_deals=combined, is_general_feed=True)

            # Mode B: Targeted Watchlist Items Search
            # If no items detected at all, default to general frontpage
            if not watchlist_items:
                _log(log_path, "Slickdeals: No specific items found in watchlist or prompt; pulling Frontpage deals...")
                fp_deals = await fetch_frontpage_deals(client, limit=20)
                elapsed = (datetime.now() - start_time).total_seconds()
                _log(log_path, f"Slickdeals: Finished in {elapsed:.2f}s with {len(fp_deals)} Frontpage deals.")
                return format_deals_report({}, [], curated_deals=fp_deals, is_general_feed=True)

            _log(log_path, f"Slickdeals: Executing Targeted Watchlist search for {len(watchlist_items)} item(s)...")

            # Stage 1: Extract high-recall search terms via LLM (omitting secondary details)
            _log(log_path, "Slickdeals: Stage 1 - Extracting optimized search terms via LLM...")
            search_terms = await extract_search_terms_with_llm(watchlist_items, log_path=log_path)

            # Concurrently search RSS feeds for all items using optimized search terms + fetch frontpage
            search_tasks = [
                search_deals_for_item(search_terms.get(item, item), client, max_results=5, log_path=log_path)
                for item in watchlist_items
            ]
            fp_task = fetch_frontpage_deals(client, limit=30)

            results = await asyncio.gather(*search_tasks, fp_task)
            item_deals_list = results[:-1]
            frontpage_deals = results[-1]

            # Stage 2: Validate candidate deals against full target item details via LLM
            _log(log_path, "Slickdeals: Stage 2 - Validating candidates against item details via LLM...")
            val_tasks = [
                validate_candidate_deals_with_llm(item, deals, log_path=log_path)
                for item, deals in zip(watchlist_items, item_deals_list)
            ]
            validated_deals_list = await asyncio.gather(*val_tasks)

            target_results: Dict[str, List[Dict[str, Any]]] = {}
            for item_name, deals in zip(watchlist_items, validated_deals_list):
                target_results[item_name] = deals[:3]

            # Cross-reference frontpage matches and validate with LLM if potential matches exist
            frontpage_candidates: List[Tuple[str, Dict[str, Any]]] = []
            for fp in frontpage_deals:
                fp_title_lower = fp["title"].lower()
                for item in watchlist_items:
                    search_kw = search_terms.get(item, item).split()[0].lower()
                    if search_kw in fp_title_lower:
                        frontpage_candidates.append((item, fp))

            frontpage_matches: List[Dict[str, Any]] = []
            if frontpage_candidates:
                fp_val_tasks = [
                    validate_candidate_deals_with_llm(target_item, [deal], log_path=log_path)
                    for target_item, deal in frontpage_candidates
                ]
                fp_val_results = await asyncio.gather(*fp_val_tasks)
                for (target_item, deal), val in zip(frontpage_candidates, fp_val_results):
                    if val and deal not in frontpage_matches:
                        frontpage_matches.append(deal)

            elapsed = (datetime.now() - start_time).total_seconds()
            _log(log_path, f"Slickdeals: Watchlist search completed in {elapsed:.2f}s.")
            return format_deals_report(target_results, frontpage_matches, is_general_feed=False)

    def evaluate_outcome(self, result_text: str) -> Optional[str]:
        """
        Deterministically evaluates outcome based on summary line:
        'X items searched | Y deals found | Z not found'
        """
        match = re.search(r'(\d+)\s+items\s+searched\s+\|\s+(\d+)\s+deals\s+found\s+\|\s+(\d+)\s+not\s+found', result_text)
        if match:
            searched = int(match.group(1))
            found = int(match.group(2))
            not_found = int(match.group(3))

            if searched > 0 and found == searched and not_found == 0:
                return "SUCCESS"
            elif found > 0:
                return "PARTIAL_SUCCESS"
            elif found == 0:
                return "NO_RESULT"

        if "No active deals found" in result_text and "deals found" not in result_text:
            return "NO_RESULT"

        return None
