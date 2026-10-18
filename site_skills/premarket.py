# site_skills/premarket.py
import asyncio
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
import json
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import httpx

from backend import config
from backend.core.plugin import BaseSitePlugin
from backend.services.llm import clean_json_text
from browser_use import BrowserSession

load_dotenv()
logger = logging.getLogger(__name__)

# ==========================================
# 1. Endpoints, Keywords & Precompiled Regexes
# ==========================================

FINNHUB_NEWS_API_URL = "https://finnhub.io/api/v1/news"
FINNHUB_COMPANY_NEWS_API_URL = "https://finnhub.io/api/v1/company-news"
GOOGLE_FINANCE_RSS_URL = "https://news.google.com/rss/headlines/section/topic/BUSINESS"

ET_TZ = ZoneInfo("America/New_York")

# Broad market index tickers (treated as macro, not single companies)
BROAD_MARKET_TICKERS = {"SPY", "QQQ", "DIA", "IWM", "VIX", "TLT", "DXY", "USO", "GLD"}

# Common English words/acronyms to ignore when extracting tickers
TICKER_IGNORE_WORDS = {
    "I", "A", "TO", "THE", "FOR", "AND", "ON", "IN", "AT", "BY", "IS", "IT", "OF", "OR", "BE", 
    "AS", "DO", "IF", "MY", "SO", "UP", "NO", "US", "AM", "PM", "ET", "EST", "EDT", "NOT", "ALL", 
    "CAN", "HAS", "HAD", "BUT", "GET", "SEE", "NEW", "NOW", "DAY", "TOP", "OUT", "ANY", "SET", 
    "RUN", "API", "LLM", "USD", "ETF", "BUY", "HOLD", "SELL", "NEWS", "STOCK", "STOCKS", "WATCH",
    "MARKET", "WATCHLIST", "MORNING", "CLOSE", "ALERT", "ALERTS", "MACRO", "DEALS", "PRICE", "AI",
    "LIST", "CONTEXT", "CONTEXTS", "PRIOR", "KNOWLEDGE", "CUSTOM", "DATA", "ACTION", "ACTIONS", "INFORM"
}

# Keywords that define macroeconomic and market-wide news
MACRO_KEYWORDS = [
    "fed", "federal reserve", "fomc", "powell", "interest rate", "rate cut", "rate hike", 
    "monetary policy", "central bank", "ecb", "boj", "treasury", "treasuries", "bond yield", "yield curve",
    "inflation", "cpi", "ppi", "pce", "gdp", "jobs report", "nonfarm", "unemployment", "payroll",
    "labor market", "consumer price", "producer price", "consumer spending", "retail sales", 
    "consumer confidence", "recession", "soft landing", "economic growth", "economic data",
    "debt ceiling", "budget deficit", "fiscal policy",
    "stock market", "wall street", "s&p 500", "s&p", "sp500", "nasdaq", "dow jones", "dow", 
    "russell", "vix", "market futures", "pre-market", "premarket", "opening bell", "market wrap",
    "stocks rise", "stocks fall", "stocks tumble", "stocks rally", "stocks slump", "stocks mixed",
    "global markets", "european stocks", "asian stocks", "equities",
    "oil", "crude", "brent", "wti", "opec", "strategic reserve", "strategic petroleum", 
    "gasoline", "diesel", "natural gas", "commodities", "gold", "dollar", "usd", "dxy", "forex", "currency",
    "tariff", "tariffs", "trade war", "sanctions", "trade deficit", "export controls",
    "geopolitical", "middle east", "blockade", "defense spending", "sovereign wealth",
    "wealth fund", "regulatory", "antitrust", "cybersecurity policy", "national security", "iran"
]

COMPANY_SPECIFIC_PATTERNS = [
    r'\bq[1-4]\s+(?:earnings|results|profit|revenue)\b',
    r'\bearnings\s+(?:miss|beat|report|preview|call)\b',
    r'\b(?:acquires|acquisition of|merger with|to buy|deal spread)\b',
    r'\b(?:appoints|names|brings in|steps down|resigns as)\s+(?:ceo|cfo|chief|executive|head)\b',
    r'\b(?:upgraded|downgraded|price target|analyst rating)\b',
    r'\bshares\s+(?:surge|slide|soar|dip|jump|fall|tumble)\s+after\b',
]

# Precompiled regexes for single-pass classification
RE_MACRO = re.compile(r'\b(?:' + '|'.join(re.escape(k) for k in MACRO_KEYWORDS) + r')\b', re.IGNORECASE)
RE_COMPANY = re.compile('|'.join(COMPANY_SPECIFIC_PATTERNS), re.IGNORECASE)
RE_CONTEXT_BLOCK = re.compile(r'---\s*([^\n-]+)\s*---\s*\n(.*?)(?=\n\s*---|\n\s*USE THIS|\Z)', re.IGNORECASE | re.DOTALL)
RE_WATCHLIST_LABEL = re.compile(
    r'(?:stocks?|tickers?|watchlist|portfolio|holdings?)\s*(?:to\s*watch)?\s*[:=\-]\s*([A-Za-z0-9\s,/\$]+)|\b(?:watch|track|following)\b\s*[:=\-]?\s*([A-Za-z0-9\s,/\$]+)',
    re.IGNORECASE
)


# ==========================================
# 2. Helpers (Logging, Windows, Watchlist)
# ==========================================

def _log(log_path: str, msg: str) -> None:
    """Safe append to execution log file."""
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass


def calculate_premarket_window(now_et: Optional[datetime] = None) -> Tuple[datetime, datetime, int, int, str]:
    """Calculates pre-market window: from previous close (4:00 PM ET) up to current time."""
    if now_et is None:
        now_et = datetime.now(ET_TZ)
    elif now_et.tzinfo is None:
        now_et = now_et.replace(tzinfo=ET_TZ)

    weekday = now_et.weekday()
    days_back = 3 if weekday == 0 else (2 if weekday == 6 else 1)
    prev_close_date = now_et.date() - timedelta(days=days_back)
    day_desc = f"Friday ({prev_close_date.strftime('%b %d')})" if weekday in (0, 5, 6) else f"Yesterday ({prev_close_date.strftime('%b %d')})"

    start_dt = datetime.combine(prev_close_date, time(16, 0, 0), tzinfo=ET_TZ)
    end_dt = now_et
    start_ts, end_ts = int(start_dt.timestamp()), int(end_dt.timestamp())

    window_label = f"{day_desc} Post-Market ({start_dt.strftime('%I:%M %p %Z')}) ➔ Today ({end_dt.strftime('%b %d, %I:%M %p %Z')})"
    return start_dt, end_dt, start_ts, end_ts, window_label


def extract_tickers_from_text(text: str) -> List[str]:
    """Extract valid stock ticker symbols strictly from explicit formatting or context structures."""
    if not text:
        return []

    candidates = set()

    # 1. Explicit $TICKER
    for match in re.findall(r'\$([A-Za-z]{1,5})\b', text):
        clean = match.upper()
        if clean not in TICKER_IGNORE_WORDS:
            candidates.add(clean)

    # 2. Bound Context Blocks: --- Context Name --- \n content
    for header, content in RE_CONTEXT_BLOCK.findall(text):
        if any(kw in header.lower() for kw in ["watchlist", "stock", "ticker", "portfolio", "holding", "equities"]):
            for token in re.split(r'[\s,;\n\-\*]+', content):
                clean = re.sub(r'[^A-Za-z]', '', token).upper()
                if 1 <= len(clean) <= 5 and clean not in TICKER_IGNORE_WORDS:
                    candidates.add(clean)

    # 3. Labeled lists
    for match in RE_WATCHLIST_LABEL.finditer(text):
        section = match.group(1) or match.group(2) or ""
        for token in re.split(r'[\s,]+', section):
            clean = re.sub(r'[^A-Za-z]', '', token).upper()
            if 1 <= len(clean) <= 5 and clean not in TICKER_IGNORE_WORDS:
                candidates.add(clean)

    return sorted(list(candidates))


def get_watchlist_from_db(db_path: str = "tasks.db", log_path: str = "") -> List[str]:
    """Query Context table in tasks.db for any stock watchlist entries."""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name, content FROM contexts")
        rows = cursor.fetchall()
        conn.close()

        grocery_kws = ["safeway", "weee", "grocery", "groceries", "shopping", "food"]
        stock_kws = ["stock", "ticker", "portfolio", "equity", "equities", "watchlist", "stocks to watch", "holdings"]

        for name, content in rows:
            name_lower = name.lower()
            if any(g in name_lower for g in grocery_kws):
                continue
            if any(kw in name_lower for kw in stock_kws):
                tickers = extract_tickers_from_text(f"--- {name} ---\n{content}\n")
                if tickers:
                    _log(log_path, f"PreMarket: Found stock watchlist in Context '{name}': {', '.join(tickers)}\n")
                    return tickers
    except Exception as e:
        logger.warning(f"Error querying contexts from db: {e}")
    return []


def resolve_watchlist_tickers(prompt: str, context_str: str, log_path: str = "") -> List[str]:
    """Determines watchlist tickers strictly from Context, NOT prompt."""
    context_tickers = extract_tickers_from_text(context_str)
    if context_tickers:
        return context_tickers
    return get_watchlist_from_db(log_path=log_path)


# ==========================================
# 3. News Ingestion (Finnhub & RSS)
# ==========================================

async def fetch_finnhub_news(
    api_key: str,
    tickers: Optional[List[str]] = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    log_path: str = ""
) -> List[Dict[str, Any]]:
    """Fetch general macro news and company news from Finnhub REST API."""
    collected_items: List[Dict[str, Any]] = []
    seen_ids = set()

    _log(log_path, "Finnhub API: Fetching category 'general' news...\n")
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            resp = await client.get(f"{FINNHUB_NEWS_API_URL}?category=general&token={api_key}")
            if resp.status_code == 200:
                for item in resp.json():
                    item_id = item.get("id") or item.get("headline")
                    if item_id and item_id not in seen_ids:
                        seen_ids.add(item_id)
                        collected_items.append(item)
        except Exception as e:
            logger.error(f"Error fetching Finnhub general news: {e}")

        if tickers and start_dt and end_dt:
            from_d = start_dt.strftime("%Y-%m-%d")
            to_d = end_dt.strftime("%Y-%m-%d")
            for ticker in tickers[:8]:
                _log(log_path, f"Finnhub API: Fetching watchlist company news for '{ticker}'...\n")
                try:
                    resp = await client.get(f"{FINNHUB_COMPANY_NEWS_API_URL}?symbol={ticker}&from={from_d}&to={to_d}&token={api_key}")
                    if resp.status_code == 200:
                        for item in resp.json():
                            item_id = item.get("id") or item.get("headline")
                            if item_id and item_id not in seen_ids:
                                seen_ids.add(item_id)
                                item["related"] = ticker
                                collected_items.append(item)
                except Exception as e:
                    logger.error(f"Error fetching Finnhub company news for '{ticker}': {e}")

    return collected_items


async def fetch_rss_market_news(log_path: str = "") -> List[Dict[str, Any]]:
    """Fallback: Fetch financial news from Google News Business RSS."""
    _log(log_path, "RSS Fallback: Fetching financial news from Google News Business RSS...\n")
    items: List[Dict[str, Any]] = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(GOOGLE_FINANCE_RSS_URL)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                if channel is not None:
                    for item in channel.findall("item"):
                        title = (item.findtext("title") or "").strip()
                        link = (item.findtext("link") or "").strip()
                        pub_date_str = (item.findtext("pubDate") or "").strip()
                        source_el = item.find("source")
                        source = source_el.text.strip() if source_el is not None and source_el.text else "Market Wire"

                        item_ts = 0
                        if pub_date_str:
                            try:
                                item_ts = int(parsedate_to_datetime(pub_date_str).timestamp())
                            except Exception:
                                pass

                        clean_title = re.sub(r'\s*-\s*[^-]+$', '', title).strip()
                        items.append({
                            "id": link or clean_title,
                            "headline": clean_title,
                            "summary": clean_title,
                            "source": source,
                            "url": link,
                            "datetime": item_ts,
                            "category": "general",
                            "related": ""
                        })
    except Exception as e:
        logger.error(f"Error parsing RSS fallback news: {e}")
    return items


# ==========================================
# 4. Classification & LLM Selection
# ==========================================

def classify_and_filter_news(
    news_items: List[Dict[str, Any]],
    start_ts: int,
    end_ts: int,
    watchlist_tickers: Optional[List[str]] = None,
    max_macro_candidates: int = 15,
    max_stock_candidates: int = 8
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Filters articles within time window into Macro and Watchlist candidate pools."""
    macro_items: List[Dict[str, Any]] = []
    stock_candidates: Dict[str, List[Dict[str, Any]]] = {t: [] for t in (watchlist_tickers or [])}
    seen_macro_ids = set()
    watchlist_set = set(t.upper() for t in (watchlist_tickers or []))

    for item in news_items:
        ts = item.get("datetime", 0)
        if not ((start_ts - 900) <= ts <= (end_ts + 300)):
            continue

        headline = (item.get("headline") or "").strip()
        summary = (item.get("summary") or "").strip()
        if not headline:
            continue

        related = (item.get("related") or "").strip().upper()
        related_tickers = set(t.strip() for t in related.split(",") if t.strip()) if related else set()
        text = f"{headline} {summary}"

        # 1. Watchlist match
        if watchlist_set:
            matching = (related_tickers & watchlist_set) or {
                t for t in watchlist_set if re.search(r'\b' + re.escape(t) + r'\b', headline.upper())
            }
            if matching:
                for t in matching:
                    if t in stock_candidates and len(stock_candidates[t]) < max_stock_candidates:
                        item_copy = dict(item)
                        item_copy["matched_tickers"] = [t]
                        stock_candidates[t].append(item_copy)
                continue

        # 2. Skip non-watchlist single company events
        is_company_action = bool(RE_COMPANY.search(text))
        non_broad = related_tickers - BROAD_MARKET_TICKERS
        if is_company_action and len(non_broad) > 0:
            continue

        # 3. Macro keywords match
        if RE_MACRO.search(text):
            item_id = item.get("id") or headline
            if item_id not in seen_macro_ids:
                seen_macro_ids.add(item_id)
                macro_items.append(item)
            continue

        if len(non_broad) > 0:
            continue

    macro_items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
    for t in stock_candidates:
        stock_candidates[t].sort(key=lambda x: x.get("datetime", 0), reverse=True)

    return macro_items[:max_macro_candidates], stock_candidates


def _distinct_items(existing: List[Dict[str, Any]], candidates: List[Dict[str, Any]], target_len: int) -> List[Dict[str, Any]]:
    """Fill existing list up to target_len with distinct items from candidates."""
    res = list(existing or [])
    seen = {it.get("id") or it.get("headline") for it in res}
    for it in candidates:
        if len(res) >= target_len:
            break
        it_id = it.get("id") or it.get("headline")
        if it_id not in seen:
            seen.add(it_id)
            res.append(it)
    return res


async def select_top_significant_news(
    macro_candidates: List[Dict[str, Any]],
    stock_candidates: Dict[str, List[Dict[str, Any]]],
    log_path: str = ""
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Uses LLM to select Top 3 Macro and Top 2 per-stock articles, falling back to recency."""
    if len(macro_candidates) <= 3 and all(len(items) <= 2 for items in stock_candidates.values()):
        return macro_candidates, stock_candidates

    prompt_lines = ["Here are the candidate news articles for today's pre-market session:", "\n=== MACRO CANDIDATES ==="]
    macro_map = {}
    for i, it in enumerate(macro_candidates, 1):
        mid = f"M{i}"
        macro_map[mid] = it
        prompt_lines.append(f"[{mid}] ({it.get('source', 'News')}) {it.get('headline', '')} - {(it.get('summary') or '')[:150]}")

    stock_map = {}
    for ticker, items in stock_candidates.items():
        stock_map[ticker] = {}
        if items:
            prompt_lines.append(f"\n=== CANDIDATES FOR {ticker} ===")
            for j, it in enumerate(items, 1):
                sid = f"{ticker}-{j}"
                stock_map[ticker][sid] = it
                prompt_lines.append(f"[{sid}] ({it.get('source', 'News')}) {it.get('headline', '')}")

    system_prompt = (
        "You are an elite Wall Street financial editor.\n"
        "Pick ONLY the most significant, market-moving news items:\n"
        "1. Top 3 most significant MACRO news items (monetary policy, inflation, rates, geopolitical shifts).\n"
        "2. Up to 2 most significant news items for EACH tracked stock.\n\n"
        "Respond with a valid JSON object ONLY:\n"
        "{\"macro\": [\"M1\", \"M2\", \"M3\"], \"stocks\": {\"TICKER\": [\"TICKER-1\", \"TICKER-2\"]}}"
    )

    selected_macro, selected_stocks = None, None
    try:
        async with httpx.AsyncClient(timeout=min(config.LLM_TIMEOUT, 30.0)) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "\n".join(prompt_lines)}
                    ],
                    "temperature": 0.1,
                    "stream": False
                }
            )
            if resp.status_code == 200:
                raw = (resp.json().get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                res_json = json.loads(clean_json_text(raw))
                selected_macro = [macro_map[m] for m in res_json.get("macro", []) if m in macro_map][:3]
                selected_stocks = {
                    t: [s_map[sid] for sid in res_json.get("stocks", {}).get(t, []) if sid in s_map][:2]
                    for t, s_map in stock_map.items()
                }
                _log(log_path, f"PreMarket Plugin: LLM selected top {len(selected_macro)} macro articles.\n")
    except Exception as e:
        logger.warning(f"Error in LLM news selection: {e}")
        _log(log_path, f"PreMarket Plugin: LLM news selection fallback: {e}\n")

    # Fallback padding
    selected_macro = _distinct_items(selected_macro or [], macro_candidates, min(3, len(macro_candidates)))
    if selected_stocks is None:
        selected_stocks = {}
    for t in stock_candidates:
        selected_stocks[t] = _distinct_items(selected_stocks.get(t, []), stock_candidates[t], min(2, len(stock_candidates[t])))

    return selected_macro, selected_stocks


# ==========================================
# 5. Briefing & Report Formatter
# ==========================================

async def generate_llm_premarket_briefing(
    macro_news: List[Dict[str, Any]],
    watchlist_news_by_stock: Dict[str, List[Dict[str, Any]]],
    watchlist_tickers: List[str],
    timewindow_label: str,
    log_path: str = ""
) -> str:
    """Synthesizes a compact, high-signal pre-market macro briefing using local LLM."""
    total_stock_items = sum(len(v) for v in watchlist_news_by_stock.values())
    if not macro_news and total_stock_items == 0:
        return ""

    digest_lines = []
    for i, it in enumerate(macro_news[:3]):
        pub_dt = datetime.fromtimestamp(it.get("datetime", 0), tz=ET_TZ).strftime("%I:%M %p ET")
        digest_lines.append(f"Macro {i+1}. [{pub_dt}] ({it.get('source', 'News')}) {it.get('headline', '')} - {(it.get('summary') or '')[:180]}")

    for ticker, items in watchlist_news_by_stock.items():
        for i, it in enumerate(items[:2]):
            pub_dt = datetime.fromtimestamp(it.get("datetime", 0), tz=ET_TZ).strftime("%I:%M %p ET")
            digest_lines.append(f"Watchlist [{ticker}] {i+1}. [{pub_dt}] ({it.get('source', 'News')}) {it.get('headline', '')}")

    news_text = "\n".join(digest_lines)
    watchlist_instruction = (
        f"### 🎯 Macro News Impact on Tracked Stocks ({', '.join(watchlist_tickers)})\n"
        f"For EACH tracked ticker ({', '.join(watchlist_tickers)}), analyze and predict what impact recent macroeconomic developments (monetary policy, Treasury yields, inflation, commodities, currencies, geopolitics) will have on that stock today:\n"
        f"- **<TICKER>** [Impact: Bullish | Bearish | Mixed | Neutral]: 1-2 concise analytical sentences detailing the transmission mechanism from recent macro news/drivers to this stock and its outlook today.\n\n"
        if watchlist_tickers else ""
    )

    system_prompt = (
        "You are an elite Wall Street macro analyst.\n"
        "Generate a concise, high-signal 'Pre-Market Macroeconomic Briefing' based on the provided macro news.\n"
        "Keep the briefing tight and actionable (max 3-4 bullet points per section).\n"
        "If tracked stocks are provided, you MUST analyze each individual tracked stock's sensitivity and predicted reaction to the prevailing macroeconomic backdrop.\n\n"
        "FORMAT (Markdown):\n"
        "### 🌅 Market Sentiment & Tone\n"
        "- Tone: (Bullish / Bearish / Cautious) and the primary market driver.\n\n"
        "### 🏛️ Key Macro & Economic Drivers\n"
        "- 2-3 bullet points on monetary policy, rates, inflation, commodities, or geopolitical market impact.\n\n"
        f"{watchlist_instruction}"
        "### ⚠️ Key Catalysts To Watch Today\n"
        "- 1-2 major catalysts or data releases to monitor into the opening bell."
    )

    try:
        async with httpx.AsyncClient(timeout=min(config.LLM_TIMEOUT, 20.0)) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"TARGET TIME WINDOW: {timewindow_label}\nTRACKED STOCKS: {', '.join(watchlist_tickers) if watchlist_tickers else 'None'}\n\nCOLLECTED MACRO & WATCHLIST DIGEST:\n{news_text}"}
                    ],
                    "temperature": 0.2,
                    "stream": False
                }
            )
            if resp.status_code == 200:
                raw = (resp.json().get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                content = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
                if content and "Market Sentiment" in content:
                    _log(log_path, "PreMarket Plugin: LLM executive briefing generated successfully.\n")
                    return content
    except Exception as e:
        logger.warning(f"LLM briefing error: {e}")
        _log(log_path, f"PreMarket Plugin: LLM briefing fallback: {e}\n")

    fallback = [
        "### 🌅 Market Sentiment & Tone",
        "- **Tone**: Active pre-market session driven by macro policies, global commodities, and geopolitical developments.",
        "",
        "### 🏛️ Key Macro & Economic Drivers"
    ]
    for it in macro_news[:3]:
        fallback.append(f"- **{it.get('headline')}** ({it.get('source', 'Market')})")

    return "\n".join(fallback)


def _format_news_item(idx: int, item: Dict[str, Any]) -> List[str]:
    """Render a single news item."""
    headline = item.get("headline", "Untitled").strip()
    summary = item.get("summary", "").strip()
    source = item.get("source", "Market Wire")
    url = item.get("url", "")
    ts = item.get("datetime", 0)
    time_str = datetime.fromtimestamp(ts, tz=ET_TZ).strftime("%I:%M %p ET") if ts else "Recent"

    title_line = f"**{idx}. [{headline}]({url})**" if url else f"**{idx}. {headline}**"
    lines = [title_line, f"> 🕒 *{time_str}* | 🏛️ *{source}*"]
    if summary and summary != headline and len(summary) > 20:
        clean_sum = summary if len(summary) <= 180 else summary[:177] + "..."
        lines.append(f"> {clean_sum}")
    lines.append("")
    return lines


def format_premarket_report(
    macro_news: List[Dict[str, Any]],
    watchlist_news_by_stock: Dict[str, List[Dict[str, Any]]],
    watchlist_tickers: List[str],
    start_dt: datetime,
    end_dt: datetime,
    window_label: str,
    llm_briefing: str,
    data_source: str = "Finnhub API"
) -> str:
    """Formats the collected pre-market data into a clean, concise markdown report."""
    total_stock_count = sum(len(v) for v in watchlist_news_by_stock.values())
    total_count = len(macro_news) + total_stock_count

    lines = [
        "# 📈 Pre-Market Macro Intelligence Briefing",
        f"**Target Window**: {window_label}",
        f"**Data Provider**: `{data_source}` | **Generated**: {end_dt.strftime('%Y-%m-%d %I:%M %p %Z')}",
        "", "---", ""
    ]

    if llm_briefing:
        lines.extend([llm_briefing, "", "---", ""])

    # Macro News Section
    lines.extend([f"### 🏛️ Macroeconomic News (Top {len(macro_news)} items)", ""])
    if not macro_news:
        lines.append("> No significant macroeconomic headlines were published within this time window.")
    else:
        for i, it in enumerate(macro_news[:3], 1):
            lines.extend(_format_news_item(i, it))

    # Watchlist News Section
    if watchlist_tickers:
        lines.extend(["### 🎯 Watchlist Stock News (Top 2 per stock)", ""])
        for ticker in watchlist_tickers:
            items = watchlist_news_by_stock.get(ticker, [])
            lines.append(f"#### 🔹 {ticker} ({len(items)} items)")
            if not items:
                lines.append(f"> No company-specific overnight press releases. Trading primarily influenced by broader macro drivers (see briefing above).\n")
            else:
                for i, it in enumerate(items[:2], 1):
                    lines.extend(_format_news_item(i, it))
    else:
        lines.extend([
            "> ℹ️ *Tip: Only macro news included. To track specific stocks, add a 'Watchlist' or 'Stocks to watch' Context entry in the dashboard.*",
            ""
        ])

    lines.append("***")
    start_str, end_str = start_dt.strftime("%b %d, %I:%M %p %Z"), end_dt.strftime("%b %d, %I:%M %p %Z")
    if watchlist_tickers:
        lines.append(f"📊 **SUMMARY:** {total_count} curated news items selected ({len(macro_news)} macro, {total_stock_count} watchlist) from {start_str} to {end_str}.")
    else:
        lines.append(f"📊 **SUMMARY:** {len(macro_news)} macro news items collected from {start_str} to {end_str} (0 watchlist stocks configured).")

    return "\n".join(lines)


# ==========================================
# 6. Pre-Flight Orchestrator & Plugin
# ==========================================

async def premarket_run_pre_flight(
    browser: Optional[BrowserSession] = None,
    prompt: str = "",
    context: str = "",
    log_path: str = "",
    llm: Any = None
) -> str:
    """Main pre-flight execution logic for the PreMarket skill."""
    _log(log_path, "PRE-FLIGHT (PREMARKET): Initializing Pre-Market News collector (Macro focus)...\n")

    start_dt, end_dt, start_ts, end_ts, window_label = calculate_premarket_window()
    _log(log_path, f"PRE-FLIGHT (PREMARKET): Time Window: {window_label} [{start_ts} -> {end_ts}]\n")

    watchlist_tickers = resolve_watchlist_tickers(prompt, context, log_path=log_path)
    if watchlist_tickers:
        logger.info(f"[premarket] Detected watchlist stocks to watch: {watchlist_tickers}")
        _log(log_path, f"PRE-FLIGHT (PREMARKET): Watchlist active: {', '.join(watchlist_tickers)}. Will include macro + watchlist news.\n")
    else:
        logger.info("[premarket] No stocks-to-watch context found. Strictly filtering for MACRO news only.")
        _log(log_path, "PRE-FLIGHT (PREMARKET): No watchlist context found. Strictly collecting MACRO news only (excluding individual company noise).\n")

    finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if finnhub_key:
        data_source = "Finnhub API"
        _log(log_path, "PRE-FLIGHT (PREMARKET): FINNHUB_API_KEY detected. Querying Finnhub REST API...\n")
        raw_news = await fetch_finnhub_news(
            api_key=finnhub_key, tickers=watchlist_tickers, start_dt=start_dt, end_dt=end_dt, log_path=log_path
        )
    else:
        data_source = "Google News Business RSS (Set FINNHUB_API_KEY in .env for official Finnhub feeds)"
        _log(log_path, "PRE-FLIGHT (PREMARKET): No FINNHUB_API_KEY found in .env. Using Google News Business RSS fallback...\n")
        raw_news = await fetch_rss_market_news(log_path=log_path)

    macro_candidates, stock_candidates = classify_and_filter_news(
        raw_news, start_ts=start_ts, end_ts=end_ts, watchlist_tickers=watchlist_tickers
    )

    if not macro_candidates and not any(stock_candidates.values()) and raw_news:
        last_24h_ts = int((datetime.now(ET_TZ) - timedelta(hours=24)).timestamp())
        macro_candidates, stock_candidates = classify_and_filter_news(
            raw_news, start_ts=last_24h_ts, end_ts=end_ts, watchlist_tickers=watchlist_tickers
        )

    macro_news, watchlist_news_by_stock = await select_top_significant_news(
        macro_candidates, stock_candidates, log_path=log_path
    )
    total_selected = len(macro_news) + sum(len(v) for v in watchlist_news_by_stock.values())

    llm_briefing = ""
    if macro_news or any(watchlist_news_by_stock.values()):
        llm_briefing = await generate_llm_premarket_briefing(
            macro_news, watchlist_news_by_stock, watchlist_tickers, window_label, log_path=log_path
        )

    report = format_premarket_report(
        macro_news=macro_news,
        watchlist_news_by_stock=watchlist_news_by_stock,
        watchlist_tickers=watchlist_tickers,
        start_dt=start_dt,
        end_dt=end_dt,
        window_label=window_label,
        llm_briefing=llm_briefing,
        data_source=data_source
    )
    _log(log_path, f"PRE-FLIGHT SUCCESS: Pre-Market report generated with {len(macro_news)} macro items and {total_selected - len(macro_news)} watchlist items.\n")
    return report


class PreMarketPlugin(BaseSitePlugin):
    description = "Overnight and morning pre-market financial news and macroeconomic wire."

    def register_actions(self, controller: Any) -> None:
        controller.action('premarket_get_news')(premarket_get_news)

    async def run_pre_flight(
        self, browser: Optional[BrowserSession], prompt: str, context: str, log_path: str = "", llm: Any = None
    ) -> str:
        return await premarket_run_pre_flight(browser, prompt, context, log_path, llm)

    def evaluate_outcome(self, result_text: str) -> Optional[str]:
        lower_res = result_text.lower()
        news_match = re.search(r'(\d+)\s*(?:curated\s+news\s+items\s+selected|macro\s+news\s+items\s+collected|news\s+items?\s*(?:collected|found|retrieved))', lower_res)
        if news_match:
            news_found = int(news_match.group(1))
            return "SUCCESS" if news_found > 0 else "NO_RESULT"
        if "no significant overnight or pre-market news found" in lower_res and "macro" not in lower_res:
            return "NO_RESULT"
        return None


async def premarket_get_news(category: str = "general", symbols: str = "") -> str:
    """
    SITE-SPECIFIC SKILL: Collects market related news from yesterday after-market to this morning.
    Focuses on macroeconomic news and only includes company-specific news for stocks listed in context or symbols.
    """
    context_str = f"Watchlist: {symbols}" if symbols else ""
    return await premarket_run_pre_flight(browser=None, prompt="Collect premarket macro news.", context=context_str, log_path="")
