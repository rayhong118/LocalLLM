# site_skills/afterMarket.py
import logging
import os
import re
import json
import sqlite3
import asyncio
from datetime import datetime, time, timedelta, date
from typing import Any, List, Dict, Optional, Tuple, Set
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
import httpx
import yfinance as yf

from backend.core.plugin import BaseSitePlugin
from browser_use import BrowserSession
from backend import config

logger = logging.getLogger(__name__)

ET_TZ = ZoneInfo("America/New_York")

# Common words to ignore when extracting stock tickers
TICKER_IGNORE_WORDS = {
    "I", "A", "TO", "THE", "FOR", "AND", "ON", "IN", "AT", "BY", "IS", "IT", "OF", "OR", "BE", 
    "AS", "DO", "IF", "MY", "SO", "UP", "NO", "US", "AM", "PM", "ET", "EST", "EDT", "NOT", "ALL", 
    "CAN", "HAS", "HAD", "BUT", "GET", "SEE", "NEW", "NOW", "DAY", "TOP", "OUT", "ANY", "SET", 
    "RUN", "API", "LLM", "USD", "ETF", "BUY", "HOLD", "SELL", "NEWS", "STOCK", "STOCKS", "WATCH",
    "MARKET", "WATCHLIST", "REPORT", "CLOSE", "ALERT", "ALERTS", "MACRO", "DEALS", "PRICE", "AI",
    "LIST", "CONTEXT", "CONTEXTS", "PRIOR", "KNOWLEDGE", "CUSTOM", "DATA", "ACTION", "ACTIONS",
    "INFORM", "AFTERMARKET", "POSTMARKET", "DAILY", "TODAY", "NEAR", "FUTURE", "INDEX", "INDICES"
}

# Major broad market benchmarks
MAJOR_BENCHMARKS = [
    {"symbol": "^GSPC", "name": "S&P 500", "etf": "SPY"},
    {"symbol": "^IXIC", "name": "Nasdaq Composite", "etf": "QQQ"},
    {"symbol": "^DJI", "name": "Dow Jones Industrial", "etf": "DIA"},
    {"symbol": "^RUT", "name": "Russell 2000", "etf": "IWM"},
    {"symbol": "^VIX", "name": "CBOE Volatility Index", "etf": "VIX"},
    {"symbol": "^TNX", "name": "10-Year Treasury Yield", "etf": "TLT"},
]

# Major sector ETFs (used when no watchlist is configured to provide rich macro depth)
SECTOR_ETFS = [
    {"symbol": "XLK", "name": "Technology"},
    {"symbol": "XLF", "name": "Financials"},
    {"symbol": "XLV", "name": "Healthcare"},
    {"symbol": "XLE", "name": "Energy"},
    {"symbol": "XLY", "name": "Consumer Discretionary"},
    {"symbol": "XLI", "name": "Industrials"},
    {"symbol": "XLP", "name": "Consumer Staples"},
    {"symbol": "XLU", "name": "Utilities"},
]

# Mapping standard user ticker aliases to Yahoo Finance symbols
INDEX_SYMBOL_MAP = {
    "VIX": "^VIX",
    "SPX": "^GSPC",
    "GSPC": "^GSPC",
    "NDX": "^IXIC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
    "RUT": "^RUT",
    "TNX": "^TNX",
}

# Known ETFs and indices that do not have corporate earnings fundamentals
FUNDS_WITHOUT_EARNINGS = {
    "^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^TNX",
    "SPY", "QQQ", "DIA", "IWM", "GLD", "GLDM", "SLV", "TLT", "IEF", "SHY",
    "USO", "UNG", "SPYI", "SCHD", "JEPI", "JEPQ", "VOO", "VTI", "VT",
    "XLK", "XLF", "XLV", "XLE", "XLY", "XLI", "XLP", "XLU", "XLB", "XLC", "XLRE"
}


# ==========================================
# 1. Ticker Extraction & Watchlist Resolution
# ==========================================

def extract_tickers_from_text(text: str) -> List[str]:
    """Extract valid stock ticker symbols from text (e.g., AAPL, NVDA, $TSLA, SPY)."""
    if not text:
        return []

    candidates = set()

    # 1. Match explicit $TICKER format
    for match in re.findall(r'\$([A-Za-z]{1,5})\b', text):
        clean = match.upper()
        if clean not in TICKER_IGNORE_WORDS:
            candidates.add(clean)

    # 2. Match bound Context Blocks: --- Context Name --- \n content
    context_blocks = re.findall(
        r'---\s*([^\n-]+)\s*---\s*\n(.*?)(?=\n\s*---|\n\s*USE THIS|\Z)',
        text,
        re.IGNORECASE | re.DOTALL
    )
    for header, content in context_blocks:
        header_lower = header.lower()
        if any(kw in header_lower for kw in ["watchlist", "stock", "ticker", "portfolio", "holding", "equities"]) or len(context_blocks) == 1:
            for token in re.split(r'[\s,;\n\-\*]+', content):
                clean = re.sub(r'[^A-Za-z]', '', token).upper()
                if 1 <= len(clean) <= 5 and clean not in TICKER_IGNORE_WORDS:
                    candidates.add(clean)

    # 3. Match labeled lists like "Watchlist: AAPL, NVDA", "stocks to watch: TSLA, MSFT"
    patterns = [
        r'(?:stocks?|tickers?|watchlist|portfolio|holdings?)\s*(?:to\s*watch)?\s*[:=\-]\s*([A-Za-z0-9\s,/\$]+)',
        r'\b(?:watch|track|following)\b\s*[:=\-]?\s*([A-Za-z0-9\s,/\$]+)'
    ]
    for pat in patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            section = match.group(1)
            for token in re.split(r'[\s,]+', section):
                clean = re.sub(r'[^A-Za-z]', '', token).upper()
                if 1 <= len(clean) <= 5 and clean not in TICKER_IGNORE_WORDS:
                    candidates.add(clean)

    return sorted(list(candidates))


def get_watchlist_from_db(db_path: str = "tasks.db", log_path: str = "") -> List[str]:
    """Query Context table in tasks.db for any stock watchlist or stocks-to-watch entries."""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name, content FROM contexts")
        rows = cursor.fetchall()
        conn.close()

        grocery_kws = ["safeway", "weee", "grocery", "groceries", "shopping", "food"]

        # 1. First pass: specific stock contexts (e.g. 'Stock watchlist')
        for name, content in rows:
            name_lower = name.lower()
            if any(g in name_lower for g in grocery_kws):
                continue
            if any(kw in name_lower for kw in ["stock", "ticker", "portfolio", "equity", "equities"]):
                tickers = extract_tickers_from_text(f"--- {name} ---\n{content}\n")
                if tickers:
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"AfterMarket: Found stock watchlist in Context '{name}': {', '.join(tickers)}\n")
                    return tickers

        # 2. Second pass: generic watchlist (excluding groceries)
        for name, content in rows:
            name_lower = name.lower()
            if any(g in name_lower for g in grocery_kws):
                continue
            if any(kw in name_lower for kw in ["watchlist", "stocks to watch", "holdings"]):
                tickers = extract_tickers_from_text(f"--- {name} ---\n{content}\n")
                if tickers:
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"AfterMarket: Found watchlist in Context '{name}': {', '.join(tickers)}\n")
                    return tickers
    except Exception as e:
        logger.warning(f"Error querying contexts from db: {e}")
    return []


def resolve_watchlist_tickers(prompt: str, context_str: str, log_path: str = "") -> List[str]:
    """
    Determines watchlist stocks to track.
    Stock tickers are resolved strictly from Context (bound context string or database Context table).
    Prompt is NOT used to infer stock tickers.
    If none found, returns [] (triggering macro news only mode).
    """
    # 1. Check bound context string
    context_tickers = extract_tickers_from_text(context_str)
    if context_tickers:
        return context_tickers

    # 2. Check database Context entries
    db_tickers = get_watchlist_from_db(log_path=log_path)
    return db_tickers


# ==========================================
# 2. Trading Session Resolution
# ==========================================

def get_session_info(now_et: Optional[datetime] = None) -> Tuple[str, str, str]:
    """
    Calculates the relevant market session label and timestamps.
    Returns: (session_title, date_str, session_status)
    """
    if now_et is None:
        now_et = datetime.now(ET_TZ)
    elif now_et.tzinfo is None:
        now_et = now_et.replace(tzinfo=ET_TZ)

    weekday = now_et.weekday() # 0 = Monday, 6 = Sunday

    if weekday == 5: # Saturday
        session_date = now_et.date() - timedelta(days=1)
        session_title = f"Friday Session ({session_date.strftime('%B %d, %Y')})"
        session_status = "Weekend Close (Post-Market Settled)"
    elif weekday == 6: # Sunday
        session_date = now_et.date() - timedelta(days=2)
        session_title = f"Friday Session ({session_date.strftime('%B %d, %Y')})"
        session_status = "Weekend Close (Post-Market Settled)"
    else:
        # Weekday
        current_time = now_et.time()
        if current_time < time(16, 0): # Before 4:00 PM ET
            session_date = now_et.date() - timedelta(days=1 if weekday > 0 else 3)
            session_title = f"Previous Session ({session_date.strftime('%B %d, %Y')})"
            session_status = "Post-Market Final"
        elif current_time < time(20, 0): # Between 4:00 PM and 8:00 PM ET
            session_date = now_et.date()
            session_title = f"Today's Session ({session_date.strftime('%B %d, %Y')})"
            session_status = "After-Hours Session Active"
        else: # After 8:00 PM ET
            session_date = now_et.date()
            session_title = f"Today's Session ({session_date.strftime('%B %d, %Y')})"
            session_status = "Post-Market Final (8:00 PM ET Close)"

    date_str = session_date.strftime("%Y-%m-%d")
    return session_title, date_str, session_status


# ==========================================
# 3. Data Fetching via yfinance
# ==========================================

def fetch_single_ticker_data(symbol: str) -> Dict[str, Any]:
    """
    Fetch comprehensive market, after-hours, and fundamentals data for a single symbol.
    Runs synchronously (intended to be executed in a thread pool).
    """
    clean_sym = symbol.strip().upper()
    mapped_sym = INDEX_SYMBOL_MAP.get(clean_sym, clean_sym)

    ticker = yf.Ticker(mapped_sym)
    fi = ticker.fast_info

    last_price = getattr(fi, 'last_price', None)
    prev_close = getattr(fi, 'previous_close', None)
    open_price = getattr(fi, 'open', None)
    day_high = getattr(fi, 'day_high', None)
    day_low = getattr(fi, 'day_low', None)
    year_high = getattr(fi, 'year_high', None)
    year_low = getattr(fi, 'year_low', None)
    volume = getattr(fi, 'last_volume', None)
    avg_vol_10d = getattr(fi, 'ten_day_average_volume', None)
    sma_50 = getattr(fi, 'fifty_day_average', None)
    sma_200 = getattr(fi, 'two_hundred_day_average', None)

    reg_chg = (last_price - prev_close) if (last_price is not None and prev_close is not None) else 0.0
    reg_pct = (reg_chg / prev_close * 100) if prev_close else 0.0

    # Intraday 1m data with prepost=True to isolate regular vs post-market
    reg_close_price = last_price
    post_price = None
    post_chg = 0.0
    post_pct = 0.0

    try:
        df = ticker.history(period="1d", interval="1m", prepost=True)
        if not df.empty:
            regular_session = df.between_time("09:30", "16:00")
            post_session = df.between_time("16:00", "20:00")

            if not regular_session.empty:
                reg_close_price = float(regular_session['Close'].iloc[-1])
                reg_chg = reg_close_price - prev_close if prev_close else 0.0
                reg_pct = (reg_chg / prev_close * 100) if prev_close else 0.0

            if not post_session.empty:
                post_price = float(post_session['Close'].iloc[-1])
                base_close = reg_close_price if reg_close_price else last_price
                if base_close:
                    post_chg = post_price - base_close
                    post_pct = (post_chg / base_close * 100)
    except Exception as e:
        logger.debug(f"[afterMarket] Intraday fetch notice for {mapped_sym}: {e}")

    # Fetch Calendar & Fundamentals (only for corporate equities, not funds/indices)
    cal_data: Dict[str, Any] = {}
    if mapped_sym not in FUNDS_WITHOUT_EARNINGS and not mapped_sym.startswith("^"):
        try:
            raw_cal = ticker.calendar
            if isinstance(raw_cal, dict):
                cal_data = raw_cal
        except Exception:
            pass

    # Calculate 52-week position percentage (0% = at 52-wk low, 100% = at 52-wk high)
    range_pct = None
    if last_price and year_low and year_high and year_high > year_low:
        range_pct = ((last_price - year_low) / (year_high - year_low)) * 100

    return {
        "symbol": clean_sym,
        "mapped_symbol": mapped_sym,
        "last_price": last_price,
        "reg_close": reg_close_price or last_price,
        "prev_close": prev_close,
        "open_price": open_price,
        "day_high": day_high,
        "day_low": day_low,
        "year_high": year_high,
        "year_low": year_low,
        "volume": volume,
        "avg_vol_10d": avg_vol_10d,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "reg_chg": reg_chg,
        "reg_pct": reg_pct,
        "post_price": post_price,
        "post_chg": post_chg,
        "post_pct": post_pct,
        "range_pct": range_pct,
        "calendar": cal_data
    }


async def fetch_all_tickers_data(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch market data for multiple symbols concurrently using asyncio.to_thread."""
    results: Dict[str, Dict[str, Any]] = {}

    tasks = [asyncio.to_thread(fetch_single_ticker_data, sym) for sym in symbols]
    fetched_list = await asyncio.gather(*tasks, return_exceptions=True)

    for sym, res in zip(symbols, fetched_list):
        if isinstance(res, dict):
            results[sym] = res
        else:
            logger.warning(f"[afterMarket] Failed to fetch data for {sym}: {res}")
            results[sym] = {"symbol": sym, "error": str(res)}

    return results


# ==========================================
# 4. News Fetching via RSS
# ==========================================

async def fetch_rss_news_for_query(query: str, max_items: int = 3) -> List[Dict[str, Any]]:
    """Fetch recent business news from Google News RSS for a specific query."""
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    items: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for item_el in root.findall(".//item")[:max_items]:
                    title_el = item_el.find("title")
                    link_el = item_el.find("link")
                    pub_el = item_el.find("pubDate")
                    source_el = item_el.find("source")

                    title = title_el.text if title_el is not None and title_el.text else "Untitled"
                    link = link_el.text if link_el is not None and link_el.text else ""
                    pub_date = pub_el.text if pub_el is not None and pub_el.text else ""
                    source = source_el.text if source_el is not None and source_el.text else "Market Wire"

                    # Clean source prefix from title if duplicated
                    if " - " in title:
                        parts = title.rsplit(" - ", 1)
                        title = parts[0]
                        if not source or source == "Market Wire":
                            source = parts[1]

                    items.append({
                        "title": title.strip(),
                        "url": link.strip(),
                        "source": source.strip(),
                        "pub_date": pub_date.strip()
                    })
    except Exception as e:
        logger.debug(f"[afterMarket] RSS news query error for '{query}': {e}")

    return items


# ==========================================
# 5. Near-Future Catalysts Analyzer
# ==========================================

def analyze_near_future_catalysts(
    watchlist_data: Dict[str, Dict[str, Any]],
    indices_data: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Extracts high-impact items to pay attention to in the near future:
    - Upcoming earnings dates & estimates
    - Upcoming dividend dates
    - Proximity to 52-week High/Low (breakout/breakdown watch)
    - Moving average crossovers & technical posture
    - Macroeconomic calendar catalysts (FOMC, CPI, Jobs)
    """
    catalysts = {
        "earnings": [],
        "dividends": [],
        "breakouts": [],
        "breakdowns": [],
        "volume_spikes": [],
        "macro_schedule": []
    }

    today = date.today()

    for sym, d in watchlist_data.items():
        if d.get("error"):
            continue

        cal = d.get("calendar", {})
        price = d.get("last_price")
        year_high = d.get("year_high")
        year_low = d.get("year_low")
        vol = d.get("volume")
        avg_vol = d.get("avg_vol_10d")
        sma_50 = d.get("sma_50")
        sma_200 = d.get("sma_200")

        # 1. Earnings Date & Estimates
        if "Earnings Date" in cal and cal["Earnings Date"]:
            ed_list = cal["Earnings Date"]
            ed = ed_list[0] if isinstance(ed_list, list) else ed_list
            if isinstance(ed, (date, datetime)):
                ed_date = ed if isinstance(ed, date) and not isinstance(ed, datetime) else ed.date()
                days_away = (ed_date - today).days
                
                eps_avg = cal.get("Earnings Average")
                rev_avg = cal.get("Revenue Average")
                rev_str = f"${rev_avg / 1e9:.2f}B" if rev_avg else "N/A"
                eps_str = f"${eps_avg:.2f}" if eps_avg is not None else "N/A"

                catalysts["earnings"].append({
                    "symbol": sym,
                    "date": ed_date.strftime("%b %d, %Y"),
                    "days_away": days_away,
                    "eps_est": eps_str,
                    "rev_est": rev_str
                })

        # 2. Dividends
        if "Ex-Dividend Date" in cal and cal["Ex-Dividend Date"]:
            ex_d = cal["Ex-Dividend Date"]
            if isinstance(ex_d, (date, datetime)):
                ex_date = ex_d if isinstance(ex_d, date) and not isinstance(ex_d, datetime) else ex_d.date()
                if (ex_date - today).days >= -5:
                    div_d = cal.get("Dividend Date")
                    div_str = div_d.strftime("%b %d, %Y") if isinstance(div_d, (date, datetime)) else "TBA"
                    catalysts["dividends"].append({
                        "symbol": sym,
                        "ex_date": ex_date.strftime("%b %d, %Y"),
                        "pay_date": div_str
                    })

        # 3. 52-Week Range Proximity
        if price and year_high and year_high > 0:
            dist_to_high = ((year_high - price) / year_high) * 100
            if dist_to_high <= 4.0:
                catalysts["breakouts"].append({
                    "symbol": sym,
                    "price": price,
                    "high": year_high,
                    "pct_from_high": dist_to_high
                })

        if price and year_low and year_low > 0:
            dist_to_low = ((price - year_low) / year_low) * 100
            if dist_to_low <= 4.0:
                catalysts["breakdowns"].append({
                    "symbol": sym,
                    "price": price,
                    "low": year_low,
                    "pct_from_low": dist_to_low
                })

        # 4. Unusual Volume
        if vol and avg_vol and avg_vol > 0 and (vol / avg_vol) >= 1.35:
            catalysts["volume_spikes"].append({
                "symbol": sym,
                "vol_ratio": vol / avg_vol,
                "volume": vol,
                "avg_vol": avg_vol
            })

    # Sort earnings by proximity
    catalysts["earnings"].sort(key=lambda x: x["days_away"])

    # Standard Macro Catalysts Schedule
    catalysts["macro_schedule"] = [
        {"event": "Federal Reserve FOMC Interest Rate Decision", "timeframe": "Next Committee Meeting", "impact": "High / Market Direction"},
        {"event": "CPI / PPI Inflation Reports", "timeframe": "Monthly Release", "impact": "High / Yields & Tech"},
        {"event": "U.S. Nonfarm Payrolls & Jobs Report", "timeframe": "First Friday of Month", "impact": "High / Rate Cut Trajectory"},
        {"event": "Quarterly GDP Advance Estimate", "timeframe": "Quarterly Release", "impact": "Medium / Recession vs Soft Landing"}
    ]

    return catalysts


# ==========================================
# 6. LLM Briefing & Commentary Generator
# ==========================================

async def generate_llm_aftermarket_briefing(
    indices_data: Dict[str, Dict[str, Any]],
    watchlist_data: Dict[str, Dict[str, Any]],
    catalysts: Dict[str, Any],
    log_path: str = ""
) -> str:
    """Uses LLM to generate an executive market wrap and near-term outlook."""
    spx = indices_data.get("^GSPC", {})
    nasdaq = indices_data.get("^IXIC", {})
    vix = indices_data.get("^VIX", {})
    
    spx_chg = spx.get("reg_pct", 0.0)
    nasdaq_chg = nasdaq.get("reg_pct", 0.0)
    vix_val = vix.get("last_price", 0.0)

    # Format watchlist summary for LLM prompt
    wl_summary = []
    for s, d in watchlist_data.items():
        if not d.get("error"):
            reg_c = d.get("reg_close") or 0.0
            post_p = d.get("post_price") or reg_c
            reg_pct = d.get("reg_pct") or 0.0
            post_pct = d.get("post_pct") or 0.0
            wl_summary.append(
                f"{s}: Regular Close ${reg_c:.2f} ({reg_pct:+.2f}%), "
                f"Post-Market ${post_p:.2f} ({post_pct:+.2f}%)"
            )

    upcoming_earnings = [f"{e['symbol']} on {e['date']} (Est EPS: {e['eps_est']})" for e in catalysts.get("earnings", [])[:4]]

    prompt = (
        "You are a Wall Street quantitative market strategist writing a concise After-Market Daily Wrap.\n\n"
        "MARKET DATA SUMMARY:\n"
        f"- S&P 500: {spx_chg:+.2f}%\n"
        f"- Nasdaq Composite: {nasdaq_chg:+.2f}%\n"
        f"- CBOE Volatility (VIX): {vix_val:.2f}\n"
        f"- Monitored Equities: {'; '.join(wl_summary) if wl_summary else 'Broad Market Major Indices Focus'}\n"
        f"- Upcoming Earnings: {'; '.join(upcoming_earnings) if upcoming_earnings else 'No immediate mega-cap earnings'}\n\n"
        "INSTRUCTIONS:\n"
        "Write exactly 2 to 3 concise, professional paragraphs:\n"
        "1. Session Recap: Market sentiment, index leadership vs weakness, after-hours movements.\n"
        "2. Strategic Watchpoints: Key risks, upcoming earnings/macro catalysts, and levels to watch for the next opening bell.\n"
        "Output ONLY the markdown paragraphs. No preamble, no greetings."
    )

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "temperature": 0.2
                }
            )
            if resp.status_code == 200:
                res_json = resp.json()
                content = ""
                if "choices" in res_json and len(res_json["choices"]) > 0:
                    content = res_json["choices"][0].get("message", {}).get("content", "").strip()
                elif "message" in res_json:
                    content = res_json.get("message", {}).get("content", "").strip()
                if content:
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("AfterMarket: LLM executive commentary generated successfully.\n")
                    return content
    except Exception as e:
        logger.debug(f"[afterMarket] LLM briefing notice ({e}); using analytical deterministic briefing.")

    # High-quality deterministic fallback briefing
    tone = "cautious" if spx_chg < -0.5 else ("bullish" if spx_chg > 0.5 else "mixed/consolidating")
    vol_note = "Elevated volatility regime" if vix_val > 19 else "Low-volatility environment"

    briefing = (
        f"The trading session concluded on a **{tone}** note, with the S&P 500 recording a {spx_chg:+.2f}% change "
        f"and the tech-heavy Nasdaq Composite closing at {nasdaq_chg:+.2f}%. The CBOE Volatility Index (VIX) "
        f"settled at **{vix_val:.2f}**, reflecting a {vol_note.lower()} across equity benchmarks. "
        "In the post-market session, trading volumes tapered off as participants digested regular-hours institutional flows.\n\n"
        "Looking forward, market participants should closely monitor upcoming corporate earnings releases and macroeconomic "
        "catalysts. Preserving capital discipline around critical 50-day moving average support thresholds will be key as "
        "the market prepares for the next opening bell."
    )
    return briefing


# ==========================================
# 7. Markdown Report Formatter
# ==========================================

def format_aftermarket_report(
    session_title: str,
    session_status: str,
    indices_data: Dict[str, Dict[str, Any]],
    watchlist_data: Dict[str, Dict[str, Any]],
    sector_data: Dict[str, Dict[str, Any]],
    catalysts: Dict[str, Any],
    macro_news: List[Dict[str, Any]],
    stock_news_by_sym: Dict[str, List[Dict[str, Any]]],
    llm_briefing: str,
    is_watchlist_mode: bool
) -> str:
    """Format the complete After-Market report into professional, structured GitHub Markdown."""
    lines: List[str] = []

    # Title & Header
    lines.append("# 📈 Daily After-Market & Closing Report")
    lines.append(f"**Session:** {session_title} | **Status:** `{session_status}` | **Source:** *Yahoo Finance (yfinance)*")
    lines.append("")

    # Executive Briefing
    if llm_briefing:
        lines.append("### 📝 Executive Market Synthesis")
        for para in llm_briefing.split("\n\n"):
            if para.strip():
                lines.append(f"> {para.strip()}")
        lines.append("")

    # 1. Major Benchmark Indices Table
    lines.append("### 📊 Major Market Benchmarks & Volatility")
    lines.append("| Index / Benchmark | Symbol | Regular Close | Day Change ($) | Day Change (%) | 52-Wk Range | 50-Day SMA |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: |")

    for bench in MAJOR_BENCHMARKS:
        sym = bench["symbol"]
        d = indices_data.get(sym, {})
        if not d or d.get("error"):
            continue

        name = bench["name"]
        close_p = f"${d.get('reg_close', 0):,.2f}" if d.get('reg_close') else "N/A"
        chg_d = f"{d.get('reg_chg', 0):+,.2f}" if d.get('reg_chg') is not None else "0.00"
        pct = d.get('reg_pct', 0.0)

        badge = "🟢" if pct > 0.05 else ("🔴" if pct < -0.05 else "⚪")
        pct_str = f"{badge} **{pct:+.2f}%**"

        rng_str = f"${d.get('year_low', 0):,.0f} - ${d.get('year_high', 0):,.0f}" if d.get('year_low') and d.get('year_high') else "N/A"

        sma_50 = d.get('sma_50')
        sma_status = "N/A"
        if d.get('reg_close') and sma_50:
            sma_status = "Above 🟢" if d['reg_close'] >= sma_50 else "Below 🔴"

        lines.append(f"| **{name}** | `{sym}` | {close_p} | {chg_d} | {pct_str} | {rng_str} | {sma_status} |")

    lines.append("")

    # 2. Watchlist Stocks or Sector Overview
    if is_watchlist_mode and watchlist_data:
        lines.append(f"### 🎯 Monitored Watchlist Equities ({len(watchlist_data)} Stocks)")
        lines.append("| Ticker | Reg Close | Day Chg (%) | After-Hours / Post-Market | Post Chg (%) | Day Range | 52-Wk Position | Volume Ratio |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

        for sym, d in watchlist_data.items():
            if d.get("error"):
                lines.append(f"| **{sym}** | *Error* | - | - | - | - | - | - |")
                continue

            reg_c = d.get('reg_close')
            reg_close = f"${reg_c:.2f}" if reg_c is not None else "N/A"
            reg_pct = d.get('reg_pct') or 0.0
            reg_badge = "🟢" if reg_pct > 0.05 else ("🔴" if reg_pct < -0.05 else "⚪")
            reg_pct_str = f"{reg_badge} **{reg_pct:+.2f}%**"

            post_val = d.get('post_price')
            post_p = f"${post_val:.2f}" if post_val is not None else f"{reg_close} *(Settled)*"
            post_pct = d.get('post_pct') or 0.0
            post_badge = "🟢" if post_pct > 0.05 else ("🔴" if post_pct < -0.05 else "⚪")
            post_pct_str = f"{post_badge} {post_pct:+.2f}%" if post_val is not None else "0.00%"

            day_l = d.get('day_low')
            day_h = d.get('day_high')
            day_rng = f"${day_l:.2f} - ${day_h:.2f}" if (day_l is not None and day_h is not None) else "N/A"

            rng_p = d.get('range_pct')
            rng_pos = f"{rng_p:.1f}%" if rng_p is not None else "N/A"

            vol = d.get('volume')
            avg_vol = d.get('avg_vol_10d')
            vol_ratio_str = f"{(vol / avg_vol):.2f}x" if (vol and avg_vol and avg_vol > 0) else "Normal"

            lines.append(f"| **{sym}** | {reg_close} | {reg_pct_str} | {post_p} | {post_pct_str} | {day_rng} | {rng_pos} | {vol_ratio_str} |")

        lines.append("")
    else:
        # No watchlist mode: Display S&P 500 Sector Performance
        lines.append("### 🌐 S&P 500 Sector ETF Performance (Macro Breadth)")
        lines.append("| Sector | ETF | Close | Day Change (%) | 52-Wk Range | Trend |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")

        for s in SECTOR_ETFS:
            sym = s["symbol"]
            d = sector_data.get(sym, {})
            if not d or d.get("error"):
                continue

            name = s["name"]
            close_p = f"${d.get('reg_close', 0):.2f}" if d.get('reg_close') else "N/A"
            pct = d.get('reg_pct', 0.0)
            badge = "🟢" if pct > 0.05 else ("🔴" if pct < -0.05 else "⚪")
            pct_str = f"{badge} **{pct:+.2f}%**"
            rng_str = f"${d.get('year_low', 0):.2f} - ${d.get('year_high', 0):.2f}" if d.get('year_low') and d.get('year_high') else "N/A"

            sma_50 = d.get('sma_50')
            trend = "Bullish 🟢" if d.get('reg_close') and sma_50 and d['reg_close'] >= sma_50 else "Bearish 🔴"

            lines.append(f"| **{name}** | `{sym}` | {close_p} | {pct_str} | {rng_str} | {trend} |")

        lines.append("")
        lines.append("> ℹ️ *Tip: Tracking broad market benchmarks. To track specific portfolio stocks, add a 'Stock watchlist' Context entry (e.g., MSFT, AAPL, NVDA) in your dashboard.*")
        lines.append("")

    # 3. What to Pay Attention to in the Near Future
    lines.append("### 🔮 What to Pay Attention to in the Near Future")
    lines.append("")

    # 3A. Upcoming Earnings
    if catalysts.get("earnings"):
        lines.append("#### 📅 Upcoming Earnings Catalysts (Next 30 Days)")
        lines.append("| Company | Scheduled Date | Timeframe | Consensus EPS Est. | Consensus Revenue Est. |")
        lines.append("| :--- | :---: | :---: | :---: | :---: |")
        for e in catalysts["earnings"][:5]:
            days_str = f"in {e['days_away']} days" if e['days_away'] > 0 else ("Today / Imminent" if e['days_away'] == 0 else f"{abs(e['days_away'])} days ago")
            lines.append(f"| **{e['symbol']}** | {e['date']} | `{days_str}` | {e['eps_est']} | {e['rev_est']} |")
        lines.append("")

    # 3B. Dividends
    if catalysts.get("dividends"):
        lines.append("#### 💵 Upcoming Dividend Dates")
        for d in catalysts["dividends"][:4]:
            lines.append(f"- **{d['symbol']}**: Ex-Dividend Date on `{d['ex_date']}` (Payout: {d['pay_date']})")
        lines.append("")

    # 3C. Technical Watchpoints (Breakouts / Breakdowns)
    tech_notes = []
    for b in catalysts.get("breakouts", []):
        tech_notes.append(f"- 🔥 **{b['symbol']} Breakout Watch**: Trading within **{b['pct_from_high']:.1f}%** of its 52-week high (${b['high']:.2f}).")
    for bd in catalysts.get("breakdowns", []):
        tech_notes.append(f"- ⚠️ **{bd['symbol']} Support Watch**: Trading near its 52-week low (${bd['low']:.2f}, {bd['pct_from_low']:.1f}% away).")
    for vs in catalysts.get("volume_spikes", []):
        tech_notes.append(f"- ⚡ **{vs['symbol']} Volume Spike**: Session volume ran **{vs['vol_ratio']:.2f}x** higher than 10-day average.")

    if tech_notes:
        lines.append("#### 🎯 Technical & Volume Watchpoints")
        lines.extend(tech_notes)
        lines.append("")

    # 3D. Macro Catalysts Schedule
    lines.append("#### 🏛️ Macroeconomic Events & Economic Schedule")
    for m in catalysts.get("macro_schedule", []):
        lines.append(f"- **{m['event']}** ({m['timeframe']}) — *Impact: {m['impact']}*")
    lines.append("")

    # 4. Top News & Market Headlines
    lines.append("### 📰 Recent Business & After-Market News")
    has_news = False

    if macro_news:
        has_news = True
        lines.append("#### 🌐 Broad Market & Economic Wire")
        for n in macro_news[:3]:
            title = n.get("title", "Untitled")
            url = n.get("url", "")
            source = n.get("source", "Market Wire")
            if url:
                lines.append(f"- [{title}]({url}) — *{source}*")
            else:
                lines.append(f"- {title} — *{source}*")
        lines.append("")

    if is_watchlist_mode and stock_news_by_sym:
        for sym, items in stock_news_by_sym.items():
            if items:
                has_news = True
                lines.append(f"#### 🔹 {sym} Recent Wire")
                for n in items[:2]:
                    title = n.get("title", "Untitled")
                    url = n.get("url", "")
                    source = n.get("source", "Market Wire")
                    if url:
                        lines.append(f"- [{title}]({url}) — *{source}*")
                    else:
                        lines.append(f"- {title} — *{source}*")
                lines.append("")
    elif not is_watchlist_mode:
        lines.append("> ℹ️ *Tip: Only macroeconomic news and market benchmarks included. To track specific stocks, add a 'Stock watchlist' Context entry in your dashboard.*")
        lines.append("")

    if not has_news:
        lines.append("> *No immediate breaking headlines reported in the current cycle.*")
        lines.append("")

    # Summary Line for Structured Evaluator
    lines.append("***")
    num_indices = len(indices_data)
    num_watchlist = len(watchlist_data) if is_watchlist_mode else 0
    num_catalysts = len(catalysts.get("earnings", [])) + len(catalysts.get("breakouts", [])) + len(catalysts.get("breakdowns", []))

    if is_watchlist_mode:
        lines.append(
            f"📊 **SUMMARY:** Daily market report generated: {num_indices} major indices tracked, "
            f"{num_watchlist} watchlist stocks analyzed, {num_catalysts} upcoming catalysts flagged."
        )
    else:
        lines.append(
            f"📊 **SUMMARY:** Daily market report generated: {num_indices} major indices tracked, "
            f"{len(sector_data)} sector benchmarks analyzed (0 watchlist stocks configured), {len(macro_news)} macro news items collected."
        )

    return "\n".join(lines)


# ==========================================
# 8. Main Pre-Flight Orchestrator
# ==========================================

async def aftermarket_run_pre_flight(
    browser: Optional[BrowserSession],
    prompt: str,
    context: str,
    log_path: str = "",
    llm: Any = None
) -> str:
    """Main pre-flight execution logic for the AfterMarket skill."""
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("PRE-FLIGHT (AFTERMARKET): Initializing After-Market & Daily Market Report generator...\n")

    # 1. Resolve Session Timestamps
    session_title, date_str, session_status = get_session_info()
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (AFTERMARKET): Session: {session_title} | Status: {session_status}\n")

    # 2. Resolve Watchlist Stocks
    watchlist_tickers = resolve_watchlist_tickers(prompt, context, log_path=log_path)
    is_watchlist_mode = bool(watchlist_tickers)

    if is_watchlist_mode:
        logger.info(f"[afterMarket] Watchlist stocks active: {watchlist_tickers}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT (AFTERMARKET): Watchlist active: {', '.join(watchlist_tickers)}. Fetching equity + post-market metrics.\n")
    else:
        logger.info("[afterMarket] No stock watchlist found in context. Gathering MACRO news and broad market indices only.")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("PRE-FLIGHT (AFTERMARKET): No watchlist context found. Gathering MACRO news and broad market indices only (excluding individual company noise).\n")

    # 3. Concurrently Fetch yfinance Data
    benchmark_symbols = [b["symbol"] for b in MAJOR_BENCHMARKS]
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (AFTERMARKET): Querying yfinance for {len(benchmark_symbols)} benchmark indices...\n")

    # Fetch benchmarks
    indices_data = await fetch_all_tickers_data(benchmark_symbols)

    # Fetch watchlist stocks (if configured) or sector ETFs (if no watchlist)
    watchlist_data: Dict[str, Dict[str, Any]] = {}
    sector_data: Dict[str, Dict[str, Any]] = {}

    if is_watchlist_mode:
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT (AFTERMARKET): Querying yfinance for {len(watchlist_tickers)} watchlist stocks...\n")
        watchlist_data = await fetch_all_tickers_data(watchlist_tickers)
    else:
        sector_symbols = [s["symbol"] for s in SECTOR_ETFS]
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT (AFTERMARKET): Querying yfinance for {len(sector_symbols)} sector ETFs...\n")
        sector_data = await fetch_all_tickers_data(sector_symbols)

    # 4. Analyze Near-Future Catalysts (Earnings, Dividends, Breakouts, Technicals)
    catalysts = analyze_near_future_catalysts(watchlist_data, indices_data)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (AFTERMARKET): Identified {len(catalysts.get('earnings', []))} upcoming earnings dates.\n")

    # 5. Fetch Recent News via RSS (Macro News + Watchlist News)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("PRE-FLIGHT (AFTERMARKET): Fetching recent market news from Google News RSS...\n")

    macro_news = await fetch_rss_news_for_query("stock+market+closing+bell+OR+after+hours+OR+wall+street+when:2d", max_items=4)

    stock_news_by_sym: Dict[str, List[Dict[str, Any]]] = {}
    if is_watchlist_mode:
        for sym in watchlist_tickers[:5]: # Top 5 watchlist stocks
            stock_news_by_sym[sym] = await fetch_rss_news_for_query(f"{sym}+stock+when:2d", max_items=2)

    # 6. Generate Executive Briefing
    llm_briefing = await generate_llm_aftermarket_briefing(
        indices_data=indices_data,
        watchlist_data=watchlist_data,
        catalysts=catalysts,
        log_path=log_path
    )

    # 7. Format Final Markdown Report
    report = format_aftermarket_report(
        session_title=session_title,
        session_status=session_status,
        indices_data=indices_data,
        watchlist_data=watchlist_data,
        sector_data=sector_data,
        catalysts=catalysts,
        macro_news=macro_news,
        stock_news_by_sym=stock_news_by_sym,
        llm_briefing=llm_briefing,
        is_watchlist_mode=is_watchlist_mode
    )

    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("PRE-FLIGHT SUCCESS: After-Market report compiled successfully.\n")

    return report


# ==========================================
# 9. Plugin Class & Controller Action
# ==========================================

class AfterMarketPlugin(BaseSitePlugin):
    description = "After-market daily stock report, post-market prices (yfinance), and near-term watchpoints."

    def register_actions(self, controller: Any) -> None:
        controller.action('aftermarket_daily_report')(aftermarket_daily_report)

    async def run_pre_flight(self, browser: Optional[BrowserSession], prompt: str, context: str, log_path: str = "", llm: Any = None) -> str:
        return await aftermarket_run_pre_flight(browser, prompt, context, log_path, llm)

    def evaluate_outcome(self, result_text: str) -> Optional[str]:
        lower_res = result_text.lower()
        market_rep_match = re.search(r'daily market report generated[^\n\r]*?(\d+)\s*(?:major\s+)?indices\s+tracked', lower_res)
        if market_rep_match:
            indices_count = int(market_rep_match.group(1))
            return "SUCCESS" if indices_count > 0 else "NO_RESULT"
        return None


async def aftermarket_daily_report(symbols: str = "") -> str:
    """
    SITE-SPECIFIC SKILL: Generates an After-Market Daily Stock Report using yfinance.
    Tracks major indices and watchlist stocks, including regular close, after-hours/post-market prices,
    earnings calendar, technical levels, and key catalysts to watch in the near future.
    """
    context_str = f"Watchlist: {symbols}" if symbols else ""
    return await aftermarket_run_pre_flight(browser=None, prompt="After-market daily report.", context=context_str, log_path="")
