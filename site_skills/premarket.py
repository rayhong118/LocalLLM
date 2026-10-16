# site_skills/premarket.py
import logging
import os
import re
import json
import sqlite3
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, List, Dict, Optional, Tuple, Set
from zoneinfo import ZoneInfo
import httpx
from dotenv import load_dotenv

from backend.core.plugin import BaseSitePlugin
from browser_use import BrowserSession
from backend import config

load_dotenv()
logger = logging.getLogger(__name__)

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
    "MARKET", "WATCHLIST", "MORNING", "CLOSE", "ALERT", "ALERTS", "MACRO", "DEALS", "PRICE", "AI"
}

# Keywords that define macroeconomic and market-wide news
MACRO_KEYWORDS = [
    # Monetary Policy, Central Banks & Rates
    "fed", "federal reserve", "fomc", "powell", "interest rate", "rate cut", "rate hike", 
    "monetary policy", "central bank", "ecb", "boj", "treasury", "treasuries", "bond yield", "yield curve",
    
    # Economic Indicators & Data
    "inflation", "cpi", "ppi", "pce", "gdp", "jobs report", "nonfarm", "unemployment", "payroll",
    "labor market", "consumer price", "producer price", "consumer spending", "retail sales", 
    "consumer confidence", "recession", "soft landing", "economic growth", "economic data",
    "debt ceiling", "budget deficit", "fiscal policy",
    
    # Broad Market Trends & Indexes
    "stock market", "wall street", "s&p 500", "s&p", "sp500", "nasdaq", "dow jones", "dow", 
    "russell", "vix", "market futures", "pre-market", "premarket", "opening bell", "market wrap",
    "stocks rise", "stocks fall", "stocks tumble", "stocks rally", "stocks slump", "stocks mixed",
    "global markets", "european stocks", "asian stocks", "equities",
    
    # Energy, Commodities & Currencies
    "oil", "crude", "brent", "wti", "opec", "strategic reserve", "strategic petroleum", 
    "gasoline", "diesel", "natural gas", "commodities", "gold", "dollar", "usd", "dxy", "forex", "currency",
    
    # Broad Policy & Geopolitical Events with Macro Market Impact
    "tariff", "tariffs", "trade war", "sanctions", "trade deficit", "export controls",
    "geopolitical", "middle east", "blockade", "defense spending", "sovereign wealth",
    "wealth fund", "regulatory", "antitrust", "cybersecurity policy", "national security", "iran"
]

# Patterns characteristic of single-company corporate events
COMPANY_SPECIFIC_PATTERNS = [
    r'\bq[1-4]\s+(?:earnings|results|profit|revenue)\b',
    r'\bearnings\s+(?:miss|beat|report|preview|call)\b',
    r'\b(?:acquires|acquisition of|merger with|to buy|deal spread)\b',
    r'\b(?:appoints|names|brings in|steps down|resigns as)\s+(?:ceo|cfo|chief|executive|head)\b',
    r'\b(?:upgraded|downgraded|price target|analyst rating)\b',
    r'\bshares\s+(?:surge|slide|soar|dip|jump|fall|tumble)\s+after\b',
]


def calculate_premarket_window(now_et: Optional[datetime] = None) -> Tuple[datetime, datetime, int, int, str]:
    """
    Calculates the target pre-market time window:
    From after-market close (4:00 PM ET) of the previous trading day up to the current morning time.

    - Tuesday-Friday morning: Yesterday 4:00 PM ET -> Current time.
    - Monday morning: Friday 4:00 PM ET -> Current time.
    - Saturday/Sunday: Friday 4:00 PM ET -> Current time.

    Returns:
        (start_dt, end_dt, start_ts, end_ts, window_label)
    """
    if now_et is None:
        now_et = datetime.now(ET_TZ)
    elif now_et.tzinfo is None:
        now_et = now_et.replace(tzinfo=ET_TZ)

    weekday = now_et.weekday()  # 0 = Monday, 6 = Sunday

    if weekday == 0:  # Monday
        prev_close_date = now_et.date() - timedelta(days=3)
        day_desc = f"Friday ({prev_close_date.strftime('%b %d')})"
    elif weekday == 6:  # Sunday
        prev_close_date = now_et.date() - timedelta(days=2)
        day_desc = f"Friday ({prev_close_date.strftime('%b %d')})"
    elif weekday == 5:  # Saturday
        prev_close_date = now_et.date() - timedelta(days=1)
        day_desc = f"Friday ({prev_close_date.strftime('%b %d')})"
    else:  # Tuesday through Friday
        prev_close_date = now_et.date() - timedelta(days=1)
        day_desc = f"Yesterday ({prev_close_date.strftime('%b %d')})"

    start_dt = datetime.combine(prev_close_date, time(16, 0, 0), tzinfo=ET_TZ)
    end_dt = now_et

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    window_label = (
        f"{day_desc} Post-Market ({start_dt.strftime('%I:%M %p %Z')}) "
        f"➔ Today ({end_dt.strftime('%b %d, %I:%M %p %Z')})"
    )
    return start_dt, end_dt, start_ts, end_ts, window_label


def extract_tickers_from_text(text: str) -> List[str]:
    """Extract valid stock ticker symbols from text (e.g., AAPL, NVDA, $TSLA, SPY)."""
    if not text:
        return []

    candidates = set()

    # 1. Match explicit $TICKER format
    for match in re.findall(r'\$([A-Za-z]{1,5})\b', text):
        candidates.add(match.upper())

    # 2. Match labeled lists like "Watchlist: AAPL, NVDA", "stocks to watch: TSLA, MSFT"
    patterns = [
        r'(?:stocks?|tickers?|watchlist|portfolio|holdings?)\s*(?:to\s*watch)?\s*[:=\-]\s*([A-Za-z0-9\s,/\$]+)',
        r'\b(?:watch|track|following)\s*[:=\-]?\s*([A-Za-z0-9\s,/\$]+)'
    ]
    for pat in patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            section = match.group(1)
            for token in re.split(r'[\s,]+', section):
                clean = re.sub(r'[^A-Za-z]', '', token).upper()
                if 1 <= len(clean) <= 5 and clean not in TICKER_IGNORE_WORDS:
                    candidates.add(clean)

    # 3. Known large-cap tech tickers if explicitly mentioned in query
    common_tickers = {
        "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
        "AMD", "INTC", "NFLX", "AVGO", "QCOM", "PLTR", "ARM", "COIN"
    }
    for word in re.findall(r'\b[A-Z]{2,5}\b', text):
        if word in common_tickers:
            candidates.add(word)

    return sorted(list(candidates))


def get_watchlist_from_db(db_path: str = "tasks.db", log_path: str = "") -> List[str]:
    """Query Context table in tasks.db for any watchlist or stocks-to-watch entries."""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name, content FROM contexts")
        rows = cursor.fetchall()
        conn.close()

        for name, content in rows:
            name_lower = name.lower()
            content_lower = content.lower()
            if any(kw in name_lower or kw in content_lower for kw in [
                "watchlist", "stocks to watch", "stock to watch", "portfolio", "tickers to watch", "equities to watch"
            ]):
                tickers = extract_tickers_from_text(f"{name} {content}")
                if tickers:
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"PreMarket: Found watchlist in Context '{name}': {', '.join(tickers)}\n")
                    return tickers
    except Exception as e:
        logger.warning(f"Error querying contexts from db: {e}")
    return []


def resolve_watchlist_tickers(prompt: str, context_str: str, log_path: str = "") -> List[str]:
    """
    Determines if there are existing context or prompt entries listing stocks to watch for.
    If none exist, returns an empty list.
    """
    # 1. Check prompt & passed context string first
    prompt_tickers = extract_tickers_from_text(prompt)
    if prompt_tickers:
        return prompt_tickers

    context_tickers = extract_tickers_from_text(context_str)
    if context_tickers:
        return context_tickers

    # 2. Check database Context entries
    db_tickers = get_watchlist_from_db(log_path=log_path)
    return db_tickers


async def fetch_finnhub_news(
    api_key: str,
    tickers: Optional[List[str]] = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    log_path: str = ""
) -> List[Dict[str, Any]]:
    """
    Fetch market news using Finnhub REST API.
    Fetches general market news. If specific watchlist tickers are given, queries company news for them.
    """
    collected_items: List[Dict[str, Any]] = []
    seen_ids = set()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        # 1. Fetch General Market News (macro focus)
        url = f"{FINNHUB_NEWS_API_URL}?category=general&token={api_key}"
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("Finnhub API: Fetching category 'general' news...\n")
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        item_id = item.get("id") or item.get("headline")
                        if item_id and item_id not in seen_ids:
                            seen_ids.add(item_id)
                            collected_items.append(item)
            else:
                logger.warning(f"Finnhub general news returned {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.error(f"Error fetching Finnhub general news: {e}")

        # 2. If watchlist tickers are provided, fetch company-specific news specifically for them
        if tickers and start_dt and end_dt:
            from_date = start_dt.strftime("%Y-%m-%d")
            to_date = end_dt.strftime("%Y-%m-%d")
            for ticker in tickers[:5]:  # limit to top 5 tickers to respect rate limits
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"Finnhub API: Fetching watchlist company news for '{ticker}'...\n")
                url = f"{FINNHUB_COMPANY_NEWS_API_URL}?symbol={ticker}&from={from_date}&to={to_date}&token={api_key}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            for item in data:
                                item_id = item.get("id") or item.get("headline")
                                if item_id and item_id not in seen_ids:
                                    seen_ids.add(item_id)
                                    item["related"] = ticker
                                    collected_items.append(item)
                except Exception as e:
                    logger.error(f"Error fetching Finnhub company news for '{ticker}': {e}")

    return collected_items


async def fetch_rss_market_news(log_path: str = "") -> List[Dict[str, Any]]:
    """Fallback: Fetch financial news from Google News Business RSS when FINNHUB_API_KEY is not set."""
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("RSS Fallback: Fetching financial news from Google News Business RSS...\n")

    items: List[Dict[str, Any]] = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(GOOGLE_FINANCE_RSS_URL)
            if resp.status_code != 200:
                return items

            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                return items

            for item in channel.findall("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub_date_str = (item.findtext("pubDate") or "").strip()
                source_el = item.find("source")
                source = source_el.text.strip() if source_el is not None and source_el.text else "Market Wire"

                item_ts = 0
                if pub_date_str:
                    try:
                        dt = parsedate_to_datetime(pub_date_str)
                        item_ts = int(dt.timestamp())
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


def classify_and_filter_news(
    news_items: List[Dict[str, Any]],
    start_ts: int,
    end_ts: int,
    watchlist_tickers: Optional[List[str]] = None,
    max_macro: int = 8,
    max_watchlist: int = 6
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Filters articles within the time window and categorizes them:
    1. Watchlist News: ONLY articles directly matching specified watchlist tickers.
    2. Macro News: Broader market, economy, Fed, inflation, rates, energy, geopolitics.
    3. Excluded: Stock-specific corporate news for companies NOT in watchlist.
    """
    macro_items: List[Dict[str, Any]] = []
    watchlist_items: List[Dict[str, Any]] = []

    watchlist_set = set(t.upper() for t in (watchlist_tickers or []))

    for item in news_items:
        ts = item.get("datetime", 0)
        # Time window check (allow 15-min tolerance on start)
        if not ((start_ts - 900) <= ts <= (end_ts + 300)):
            continue

        headline = (item.get("headline") or "").strip()
        summary = (item.get("summary") or "").strip()
        if not headline:
            continue

        related = (item.get("related") or "").strip().upper()
        related_tickers = set(t.strip() for t in related.split(",") if t.strip()) if related else set()
        text = f"{headline} {summary}".lower()

        # 1. Check Watchlist Match
        if watchlist_set:
            matching = (related_tickers & watchlist_set) or {
                t for t in watchlist_set if re.search(r'\b' + re.escape(t) + r'\b', headline.upper())
            }
            if matching:
                item["matched_tickers"] = list(matching)
                watchlist_items.append(item)
                continue

        # 2. Check if this is a single company corporate action (earnings, CEO hire, M&A)
        # If it's single-company corporate news and NOT on the watchlist, discard it.
        is_company_action = any(re.search(pat, text) for pat in COMPANY_SPECIFIC_PATTERNS)
        non_broad_tickers = related_tickers - BROAD_MARKET_TICKERS
        if is_company_action and len(non_broad_tickers) > 0:
            continue

        # 3. Check Macroeconomic Keywords
        is_macro = any(re.search(r'\b' + re.escape(kw) + r'\b', text) for kw in MACRO_KEYWORDS)
        if is_macro:
            macro_items.append(item)
            continue

        # If it contains specific non-broad company tickers and has no macro relevance, discard
        if len(non_broad_tickers) > 0:
            continue

    # Sort both newest first
    macro_items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
    watchlist_items.sort(key=lambda x: x.get("datetime", 0), reverse=True)

    return macro_items[:max_macro], watchlist_items[:max_watchlist]


async def generate_llm_premarket_briefing(
    macro_news: List[Dict[str, Any]],
    watchlist_news: List[Dict[str, Any]],
    watchlist_tickers: List[str],
    timewindow_label: str,
    log_path: str = ""
) -> str:
    """Synthesizes a compact, high-signal pre-market macro briefing using local LLM."""
    if not macro_news and not watchlist_news:
        return ""

    digest_lines = []
    for i, it in enumerate(macro_news[:8]):
        pub_dt = datetime.fromtimestamp(it.get("datetime", 0), tz=ET_TZ).strftime("%I:%M %p ET")
        source = it.get("source", "News")
        headline = it.get("headline", "")
        summary = (it.get("summary") or "")[:180]
        digest_lines.append(f"Macro {i+1}. [{pub_dt}] ({source}) {headline} - {summary}")

    for i, it in enumerate(watchlist_news[:5]):
        pub_dt = datetime.fromtimestamp(it.get("datetime", 0), tz=ET_TZ).strftime("%I:%M %p ET")
        source = it.get("source", "News")
        headline = it.get("headline", "")
        matched = it.get("matched_tickers", [])
        ticker_str = f" [Watchlist: {','.join(matched)}]" if matched else ""
        digest_lines.append(f"Watchlist {i+1}. [{pub_dt}] ({source}){ticker_str} {headline}")

    news_text = "\n".join(digest_lines)

    watchlist_instruction = ""
    if watchlist_tickers:
        watchlist_instruction = (
            f"### 🎯 Watchlist Focus ({', '.join(watchlist_tickers)})\n"
            "- Key company overnight developments or catalysts for watchlist stocks.\n\n"
        )

    system_prompt = (
        "You are an elite Wall Street macro analyst.\n"
        "Generate a concise, high-signal 'Pre-Market Macroeconomic Briefing' based on the provided macro news.\n"
        "Keep the briefing tight and actionable (max 3-4 bullet points per section).\n\n"
        "FORMAT (Markdown):\n"
        "### 🌅 Market Sentiment & Tone\n"
        "- Tone: (Bullish / Bearish / Cautious) and the primary market driver.\n\n"
        "### 🏛️ Key Macro & Economic Drivers\n"
        "- 2-3 bullet points on monetary policy, rates, inflation, commodities, or geopolitical market impact.\n\n"
        f"{watchlist_instruction}"
        "### ⚠️ Key Catalysts To Watch Today\n"
        "- 1-2 major catalysts or data releases to monitor into the opening bell."
    )

    user_prompt = f"TARGET TIME WINDOW: {timewindow_label}\n\nCOLLECTED MACRO & WATCHLIST DIGEST:\n{news_text}"

    try:
        async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.2,
                    "stream": False
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                content = (msg.get("content") or "").strip()
                if not content and "reasoning_content" in msg:
                    content = msg["reasoning_content"].strip()

                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                if content and "Market Sentiment" in content:
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("PreMarket Plugin: LLM executive briefing generated successfully.\n")
                    return content
    except Exception as e:
        logger.warning(f"LLM briefing error: {e}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PreMarket Plugin: LLM briefing fallback: {e}\n")

    # Fallback deterministic briefing
    fallback = [
        "### 🌅 Market Sentiment & Tone",
        "- **Tone**: Active pre-market session driven by macro policies, global commodities, and geopolitical developments.",
        "",
        "### 🏛️ Key Macro & Economic Drivers"
    ]
    for it in macro_news[:3]:
        fallback.append(f"- **{it.get('headline')}** ({it.get('source', 'Market')})")
    return "\n".join(fallback)


def format_premarket_report(
    macro_news: List[Dict[str, Any]],
    watchlist_news: List[Dict[str, Any]],
    watchlist_tickers: List[str],
    start_dt: datetime,
    end_dt: datetime,
    window_label: str,
    llm_briefing: str,
    data_source: str = "Finnhub API"
) -> str:
    """Formats the collected pre-market data into a clean, concise markdown report."""
    total_count = len(macro_news) + len(watchlist_news)

    lines = [
        "# 📈 Pre-Market Macro Intelligence Briefing",
        f"**Target Window**: {window_label}",
        f"**Data Provider**: `{data_source}` | **Generated**: {end_dt.strftime('%Y-%m-%d %I:%M %p %Z')}",
        "",
        "---",
        ""
    ]

    # Executive Briefing
    if llm_briefing:
        lines.append(llm_briefing)
        lines.append("")
        lines.append("---")
        lines.append("")

    # Macro News Section
    lines.append(f"### 🏛️ Macroeconomic News ({len(macro_news)} items)")
    lines.append("")

    if not macro_news:
        lines.append("> No significant macroeconomic headlines were published within this time window.")
    else:
        for i, item in enumerate(macro_news, 1):
            headline = item.get("headline", "Untitled").strip()
            summary = item.get("summary", "").strip()
            source = item.get("source", "Market Wire")
            url = item.get("url", "")
            ts = item.get("datetime", 0)

            time_str = datetime.fromtimestamp(ts, tz=ET_TZ).strftime("%I:%M %p ET") if ts else "Recent"

            if url:
                lines.append(f"**{i}. [{headline}]({url})**")
            else:
                lines.append(f"**{i}. {headline}**")
            lines.append(f"> 🕒 *{time_str}* | 🏛️ *{source}*")
            if summary and summary != headline and len(summary) > 20:
                clean_sum = summary if len(summary) <= 180 else summary[:177] + "..."
                lines.append(f"> {clean_sum}")
            lines.append("")

    # Watchlist News Section (Only if watchlist was specified)
    if watchlist_tickers:
        lines.append(f"### 🎯 Watchlist Stock News ({len(watchlist_news)} items for {', '.join(watchlist_tickers)})")
        lines.append("")
        if not watchlist_news:
            lines.append(f"> No overnight or pre-market news found for: {', '.join(watchlist_tickers)}.")
        else:
            for i, item in enumerate(watchlist_news, 1):
                headline = item.get("headline", "Untitled").strip()
                source = item.get("source", "Market Wire")
                url = item.get("url", "")
                ts = item.get("datetime", 0)
                matched = item.get("matched_tickers", [])
                time_str = datetime.fromtimestamp(ts, tz=ET_TZ).strftime("%I:%M %p ET") if ts else "Recent"
                badge = f"`[{','.join(matched)}]` " if matched else ""

                if url:
                    lines.append(f"**{i}. {badge}[{headline}]({url})**")
                else:
                    lines.append(f"**{i}. {badge}{headline}**")
                lines.append(f"> 🕒 *{time_str}* | 🏛️ *{source}*")
                lines.append("")
    else:
        lines.append("> ℹ️ *Tip: Only macro news included. To track specific stocks, add a 'Watchlist' or 'Stocks to watch' Context entry in the dashboard.*")
        lines.append("")

    lines.append("***")
    start_str = start_dt.strftime("%b %d, %I:%M %p %Z")
    end_str = end_dt.strftime("%b %d, %I:%M %p %Z")
    if watchlist_tickers:
        lines.append(f"📊 **SUMMARY:** {total_count} market news items collected ({len(macro_news)} macro, {len(watchlist_news)} watchlist) from {start_str} to {end_str}.")
    else:
        lines.append(f"📊 **SUMMARY:** {len(macro_news)} macro news items collected from {start_str} to {end_str} (0 watchlist stocks configured).")

    return "\n".join(lines)


async def premarket_run_pre_flight(
    browser: Optional[BrowserSession],
    prompt: str,
    context: str,
    log_path: str = "",
    llm: Any = None
) -> str:
    """Main pre-flight execution logic for the PreMarket skill."""
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("PRE-FLIGHT (PREMARKET): Initializing Pre-Market News collector (Macro focus)...\n")

    # 1. Resolve Time Window
    start_dt, end_dt, start_ts, end_ts, window_label = calculate_premarket_window()
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (PREMARKET): Time Window: {window_label} [{start_ts} -> {end_ts}]\n")

    # 2. Check for Watchlist / Stocks to watch in Context or Prompt
    watchlist_tickers = resolve_watchlist_tickers(prompt, context, log_path=log_path)
    if watchlist_tickers:
        logger.info(f"[premarket] Detected watchlist stocks to watch: {watchlist_tickers}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT (PREMARKET): Watchlist active: {', '.join(watchlist_tickers)}. Will include macro + watchlist news.\n")
    else:
        logger.info("[premarket] No stocks-to-watch context found. Strictly filtering for MACRO news only.")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("PRE-FLIGHT (PREMARKET): No watchlist context found. Strictly collecting MACRO news only (excluding individual company noise).\n")

    # 3. Check for Finnhub API Key
    finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
    data_source = "Finnhub API"
    raw_news: List[Dict[str, Any]] = []

    if finnhub_key:
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("PRE-FLIGHT (PREMARKET): FINNHUB_API_KEY detected. Querying Finnhub REST API...\n")
        raw_news = await fetch_finnhub_news(
            api_key=finnhub_key,
            tickers=watchlist_tickers,
            start_dt=start_dt,
            end_dt=end_dt,
            log_path=log_path
        )
    else:
        data_source = "Google News Business RSS (Set FINNHUB_API_KEY in .env for official Finnhub feeds)"
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("PRE-FLIGHT (PREMARKET): No FINNHUB_API_KEY found in .env. Using Google News Business RSS fallback...\n")
        raw_news = await fetch_rss_market_news(log_path=log_path)

    # 4. Classify and Filter
    macro_news, watchlist_news = classify_and_filter_news(
        raw_news,
        start_ts=start_ts,
        end_ts=end_ts,
        watchlist_tickers=watchlist_tickers,
        max_macro=8,
        max_watchlist=6
    )

    total_filtered = len(macro_news) + len(watchlist_news)
    logger.info(f"[premarket] Filtered to {len(macro_news)} macro items and {len(watchlist_news)} watchlist items.")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (PREMARKET): Retained {len(macro_news)} macro news items and {len(watchlist_news)} watchlist items.\n")

    # If completely empty, try broader 24-hour window
    if total_filtered == 0 and raw_news:
        last_24h_ts = int((datetime.now(ET_TZ) - timedelta(hours=24)).timestamp())
        macro_news, watchlist_news = classify_and_filter_news(
            raw_news,
            start_ts=last_24h_ts,
            end_ts=end_ts,
            watchlist_tickers=watchlist_tickers,
            max_macro=8,
            max_watchlist=6
        )

    # 5. Generate LLM Executive Briefing
    llm_briefing = ""
    if macro_news or watchlist_news:
        llm_briefing = await generate_llm_premarket_briefing(
            macro_news, watchlist_news, watchlist_tickers, window_label, log_path=log_path
        )

    # 6. Format Markdown Report
    report = format_premarket_report(
        macro_news=macro_news,
        watchlist_news=watchlist_news,
        watchlist_tickers=watchlist_tickers,
        start_dt=start_dt,
        end_dt=end_dt,
        window_label=window_label,
        llm_briefing=llm_briefing,
        data_source=data_source
    )

    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT SUCCESS: Pre-Market report generated with {len(macro_news)} macro items and {len(watchlist_news)} watchlist items.\n")

    return report


class PreMarketPlugin(BaseSitePlugin):
    """Plugin wrapper for dynamic loading by PluginRegistry."""
    async def run_pre_flight(self, browser: BrowserSession, prompt: str, context: str, log_path: str, llm: Any) -> str:
        return await premarket_run_pre_flight(browser, prompt, context, log_path, llm)


async def premarket_get_news(category: str = "general", symbols: str = "") -> str:
    """
    SITE-SPECIFIC SKILL: Collects market related news from yesterday after-market to this morning.
    Focuses on macroeconomic news and only includes company-specific news for stocks listed in context or symbols.
    """
    prompt = f"Collect premarket macro news. Tickers: {symbols}" if symbols else "Collect premarket macro news."
    return await premarket_run_pre_flight(browser=None, prompt=prompt, context="", log_path="")
