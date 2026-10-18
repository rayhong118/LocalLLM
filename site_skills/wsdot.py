# site_skills/wsdot.py
import asyncio
from datetime import datetime, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import httpx

from backend import config
from backend.core.plugin import BaseSitePlugin
from browser_use import BrowserSession

load_dotenv()
logger = logging.getLogger(__name__)

# ==========================================
# 1. Constants & Precompiled Regexes
# ==========================================

WSDOT_ALERTS_API_URL = "http://wsdot.wa.gov/Traffic/api/HighwayAlerts/HighwayAlertsREST.svc/GetAlertsAsJson?AccessCode={access_code}"

# King County Geographic Bounding Box (Lat/Lon)
KING_LAT_MIN, KING_LAT_MAX = 47.15, 47.78
KING_LON_MIN, KING_LON_MAX = -122.53, -121.12

# Highway classification sets
INTERSTATES = {"5", "82", "90", "182", "205", "405", "705"}
US_HIGHWAYS = {"2", "12", "97", "101", "195", "395"}

DIRECTION_MAP = {
    "N": "Northbound", "NB": "Northbound",
    "S": "Southbound", "SB": "Southbound",
    "E": "Eastbound", "EB": "Eastbound",
    "W": "Westbound", "WB": "Westbound",
    "B": "Both Directions"
}

PRIORITY_ORDER = {"HIGHEST": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "LOWEST": 4}

KING_COUNTY_CITIES = [
    "seattle", "bellevue", "redmond", "kirkland", "renton", "kent", "federal way",
    "auburn", "shoreline", "burien", "bothell", "snoqualmie", "issaquah",
    "mercer island", "tukwila", "seatac", "kenmore", "woodinville", "maple valley",
    "sammamish", "north bend", "des moines", "covington", "enumclaw", "skykomish",
    "vashon", "white center", "newcastle", "fall city", "carnation", "duvall",
    "black diamond", "algona", "pacific", "milton", "normandy park", "lake forest park"
]

BELLEVUE_EASTSIDE_CITIES = [
    "bellevue", "redmond", "kirkland", "issaquah", "mercer island",
    "renton", "newcastle", "sammamish", "bothell", "woodinville",
    "factoria", "overlake", "eastgate", "medina", "clyde hill",
    "newport", "totem lake", "kenmore", "cougar mountain"
]

RE_KING_CITIES = re.compile(r'\b(?:' + '|'.join(re.escape(c) for c in KING_COUNTY_CITIES) + r')\b', re.IGNORECASE)
RE_EASTSIDE_CITIES = re.compile(r'\b(?:' + '|'.join(re.escape(c) for c in BELLEVUE_EASTSIDE_CITIES) + r')\b', re.IGNORECASE)
RE_WSDOT_DATE = re.compile(r'/Date\((\d+)')
RE_DATE_RANGE = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*(?:to|-|through)\s*(?:(jan[a-z]*|feb[a-z]*|mar[a-z]*|apr[a-z]*|may[a-z]*|jun[a-z]*|jul[a-z]*|aug[a-z]*|sep[a-z]*|oct[a-z]*|nov[a-z]*|dec[a-z]*)\s+)?(\d{1,2})(?:st|nd|rd|th)?)?(?:,?\s*(\d{4}))?\b',
    re.IGNORECASE
)

MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
}

WEEKDAY_MAP = {
    'monday': 0, 'mon': 0, 'tuesday': 1, 'tue': 1, 'wednesday': 2, 'wed': 2,
    'thursday': 3, 'thu': 3, 'friday': 4, 'fri': 4, 'saturday': 5, 'sat': 5,
    'sunday': 6, 'sun': 6
}


# ==========================================
# 2. Helpers (Logging, Formatting, Dates)
# ==========================================

def _log(log_path: str, msg: str) -> None:
    """Safe append to execution log file."""
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass


def format_highway_name(road_name: Optional[str]) -> str:
    """Format raw WSDOT RoadName (e.g., '005', '405', '090', '018') into readable Highway designation."""
    if not road_name:
        return "Local / Highway"
    cleaned = str(road_name).strip()
    digits = cleaned.lstrip('0')
    if digits in INTERSTATES:
        return f"I-{digits}"
    if digits in US_HIGHWAYS:
        return f"US {digits}"
    if digits.isdigit():
        return f"SR {digits}"
    return f"SR {cleaned}"


def parse_wsdot_datetime(date_str: Optional[str]) -> Optional[datetime]:
    """Parse Microsoft JSON date format e.g. /Date(1791576540000-0700)/ into datetime object."""
    if not date_str:
        return None
    m = RE_WSDOT_DATE.search(str(date_str))
    return datetime.fromtimestamp(int(m.group(1)) / 1000) if m else None


def parse_wsdot_date(date_str: Optional[str]) -> str:
    """Parse Microsoft JSON date into human-readable string."""
    dt = parse_wsdot_datetime(date_str)
    return dt.strftime("%b %d, %Y, %I:%M %p") if dt else (str(date_str) if date_str else "Ongoing / Until Further Notice")


def alert_overlaps_timewindow(alert: Dict[str, Any], window_start: datetime, window_end: datetime) -> bool:
    """Determines whether a highway alert overlaps with the [window_start, window_end] interval."""
    a_start = parse_wsdot_datetime(alert.get("StartTime")) or datetime.min
    a_end = parse_wsdot_datetime(alert.get("EndTime"))
    return a_start <= window_end and (a_end is None or a_end >= window_start)


def _get_alert_coords(item: Dict[str, Any]) -> List[Tuple[float, float, str]]:
    """Extract valid (lat, lon, road) tuples from start and end locations."""
    coords = []
    for loc_key in ("StartRoadwayLocation", "EndRoadwayLocation"):
        loc = item.get(loc_key)
        if isinstance(loc, dict):
            lat, lon = loc.get("Latitude"), loc.get("Longitude")
            road = str(loc.get("RoadName") or "").strip().lstrip('0')
            if lat is not None and lon is not None:
                try:
                    coords.append((float(lat), float(lon), road))
                except (ValueError, TypeError):
                    pass
            elif road:
                coords.append((0.0, 0.0, road))
    return coords


# ==========================================
# 3. Geographic Classification
# ==========================================

def is_in_king_county(item: Dict[str, Any]) -> bool:
    """Determines whether a highway alert is located in or affects King County."""
    county = item.get("County")
    if county and "king" in str(county).lower():
        return True

    for lat, lon, _ in _get_alert_coords(item):
        if lat and lon and (KING_LAT_MIN <= lat <= KING_LAT_MAX and KING_LON_MIN <= lon <= KING_LON_MAX):
            return True

    full_text = f"{item.get('HeadlineDescription', '')} {item.get('ExtendedDescription', '')}".lower()
    return "king county" in full_text or bool(RE_KING_CITIES.search(full_text))


def is_bellevue_eastside_alert(item: Dict[str, Any]) -> bool:
    """Determines whether a highway alert impacts Bellevue or the surrounding Eastside commute area."""
    full_text = f"{item.get('HeadlineDescription', '')} {item.get('ExtendedDescription', '')}".lower()
    if RE_EASTSIDE_CITIES.search(full_text):
        return True

    for lat, lon, road in _get_alert_coords(item):
        if lat and lon:
            # I-405, SR 520, SR 522 in King County Eastside
            if road in ("405", "520", "522") and (47.45 <= lat <= 47.80 and -122.35 <= lon <= -122.00):
                return True
            # I-90 Mercer Island to Issaquah
            if road == "90" and (47.50 <= lat <= 47.62 and -122.30 <= lon <= -122.00):
                return True
            # General Eastside bounding box
            if 47.50 <= lat <= 47.72 and -122.25 <= lon <= -122.05:
                return True
        elif road in ("405", "520"):
            return True

    return False


# ==========================================
# 4. Time Window Resolution
# ==========================================

async def resolve_timewindow_with_llm(query: str, now: datetime) -> Optional[Tuple[datetime, datetime, str]]:
    """Fallback: Uses local LLM to extract arbitrary or complex time windows into ISO datetimes."""
    prompt = (
        f"Current local date and time: {now.strftime('%Y-%m-%d %H:%M:%S (%A)')}.\n"
        f"User query regarding traffic timing: \"{query}\"\n\n"
        f"Extract the requested date/time window as valid JSON matching this schema:\n"
        f"{{\"start\": \"YYYY-MM-DDTHH:MM:SS\", \"end\": \"YYYY-MM-DDTHH:MM:SS\", \"label\": \"Short period description\"}}\n"
        f"Respond with JSON ONLY."
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a date and time extraction assistant. Output strictly valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 150
                }
            )
            if resp.status_code == 200:
                content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                m = re.search(r'\{.*?\}', content, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    start_dt = datetime.fromisoformat(parsed["start"])
                    end_dt = datetime.fromisoformat(parsed["end"])
                    label = parsed.get("label", f"{start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d')}")
                    return start_dt, end_dt, label
    except Exception as e:
        logger.debug(f"[wsdot] LLM timewindow resolution skipped: {e}")
    return None


async def resolve_timewindow(
    query_str: str, base_time: Optional[datetime] = None, log_path: str = ""
) -> Tuple[Optional[datetime], Optional[datetime], str]:
    """Resolves user query into (start_datetime, end_datetime, label)."""
    now = base_time or datetime.now()
    q = (query_str or "").lower().strip()
    if not q:
        return None, None, "Current Active Alerts"

    # 1. Weekend Window (covers Friday 6 PM through Monday 5 AM)
    if any(w in q for w in ["following weekend", "next weekend", "this weekend", "upcoming weekend", "weekend"]):
        weekday = now.weekday()
        if weekday == 4:
            start_fri = now
        elif weekday in (5, 6):
            if any(w in q for w in ["following weekend", "next weekend"]):
                start_fri = now + timedelta(days=(4 - weekday) % 7 + 7)
            else:
                start_fri = now - timedelta(days=weekday - 4)
        else:
            start_fri = now + timedelta(days=(4 - weekday) % 7)

        start = start_fri.replace(hour=18, minute=0, second=0, microsecond=0)
        end = (start_fri + timedelta(days=3)).replace(hour=5, minute=0, second=0, microsecond=0)
        label = f"Upcoming Weekend ({start.strftime('%b %d')} evening – {end.strftime('%b %d')} morning)"
        return start, end, label

    # 2. Tomorrow
    if "tomorrow" in q:
        tmrw = now + timedelta(days=1)
        return tmrw.replace(hour=0, minute=0, second=0, microsecond=0), tmrw.replace(hour=23, minute=59, second=59, microsecond=0), f"Tomorrow ({tmrw.strftime('%b %d, %Y')})"

    # 3. Today / Tonight
    if "today" in q or "tonight" in q:
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now.replace(hour=23, minute=59, second=59, microsecond=0), f"Today ({now.strftime('%b %d, %Y')})"

    # 4. Specific Weekday
    for day_name, day_idx in WEEKDAY_MAP.items():
        if re.search(rf"\b{day_name}\b", q):
            days_ahead = (day_idx - now.weekday()) % 7
            if days_ahead == 0 and "next" in q:
                days_ahead = 7
            target = now + timedelta(days=days_ahead)
            return target.replace(hour=0, minute=0, second=0, microsecond=0), target.replace(hour=23, minute=59, second=59, microsecond=0), target.strftime("%A, %b %d, %Y")

    # 5. Explicit Date Range (e.g. 'Oct 17', 'Oct 17-19')
    match = RE_DATE_RANGE.search(q)
    if match:
        m1_name, d1_str, m2_name, d2_str, year_str = match.groups()
        m1 = MONTH_MAP.get(m1_name[:3].lower(), now.month)
        d1 = int(d1_str)
        year = int(year_str) if year_str else now.year
        start = datetime(year, m1, d1, 0, 0, 0)
        if d2_str:
            m2 = MONTH_MAP.get(m2_name[:3].lower(), m1) if m2_name else m1
            end = datetime(year, m2, int(d2_str), 23, 59, 59)
            label = f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
        else:
            end = datetime(year, m1, d1, 23, 59, 59)
            label = start.strftime("%b %d, %Y")
        return start, end, label

    # 6. Fallback: LLM extraction for complex queries
    if any(k in q for k in ["day", "week", "month", "night", "morning", "after", "before", "between", "game", "holiday"]):
        llm_res = await resolve_timewindow_with_llm(q, now=now)
        if llm_res:
            return llm_res

    return None, None, "Current Active Alerts"


# ==========================================
# 5. Briefing & Alert Ranking
# ==========================================

def select_top_impactful_alerts(alerts: List[Dict[str, Any]], max_items: int = 5) -> List[Dict[str, Any]]:
    """Filters and ranks the most impactful alerts for commute briefing."""
    def score_alert(a: Dict[str, Any]) -> int:
        score = 0
        prio = (a.get("Priority") or "LOW").upper()
        if prio == "HIGHEST": score += 100
        elif prio == "HIGH": score += 80
        elif prio == "MEDIUM": score += 40

        cat = (a.get("EventCategory") or "").lower()
        if "closure" in cat: score += 50
        elif "accident" in cat or "collision" in cat: score += 40

        h = (a.get("HeadlineDescription") or "").lower()
        if "closed" in h or "blocking" in h: score += 30
        if "ramp" in h or "lane" in h: score += 20
        if any(hwy in h for hwy in ["405", "520", "90"]): score += 15
        return score

    return sorted(alerts, key=score_alert, reverse=True)[:max_items]


def generate_deterministic_bellevue_briefing(impactful: List[Dict[str, Any]], total_bellevue: int, timewindow_label: str = "") -> str:
    """Deterministic briefing fallback for Bellevue and Eastside commuters."""
    period_str = f" for **{timewindow_label}**" if timewindow_label else ""
    if not impactful:
        return (
            "### 🏙️ Bellevue & Eastside Commuter Briefing\n\n"
            f"✅ **All primary Eastside highway corridors (I-405, SR 520, I-90) have no critical disruptions{period_str}.**\n"
            "Traffic conditions on major routes are operating nominally."
        )

    lines = [
        "### 🏙️ Bellevue & Eastside Commuter Briefing\n",
        f"**Overview:** Found {total_bellevue} active alerts affecting Bellevue and Eastside routes{period_str}.",
        "",
        "#### 🚨 Critical Disruptions (TL;DR)"
    ]
    for a in impactful[:3]:
        road_raw = (a.get("StartRoadwayLocation") or {}).get("RoadName")
        hwy = format_highway_name(road_raw)
        h = (a.get("HeadlineDescription") or "Incident reported").strip()
        lines.append(f"- **{hwy}:** {h} *(Scheduled: {parse_wsdot_date(a.get('StartTime'))} ➔ {parse_wsdot_date(a.get('EndTime'))})*")

    lines.extend([
        "",
        "#### 💡 Commuter Advice",
        "- Allow additional travel time on I-405 and connecting ramps; monitor real-time navigation for unexpected slowdowns."
    ])
    return "\n".join(lines)


async def generate_llm_commute_briefing(bellevue_alerts: List[Dict[str, Any]], timewindow_label: str = "", log_path: str = "") -> str:
    """Uses local LLM to generate an executive commute briefing for Bellevue and Eastside drivers."""
    impactful = select_top_impactful_alerts(bellevue_alerts, max_items=4)
    if not impactful:
        return generate_deterministic_bellevue_briefing([], total_bellevue=0, timewindow_label=timewindow_label)

    alert_lines = []
    for idx, a in enumerate(impactful, 1):
        road_raw = (a.get("StartRoadwayLocation") or {}).get("RoadName")
        hwy = format_highway_name(road_raw)
        prio = (a.get("Priority") or "Medium").capitalize()
        headline = (a.get("HeadlineDescription") or "").strip()
        alert_lines.append(f"{idx}. [{hwy} - {prio}] {headline} (Schedule: {parse_wsdot_date(a.get('StartTime'))} to {parse_wsdot_date(a.get('EndTime'))})")

    timing_clause = f" active during **{timewindow_label}**" if timewindow_label else ""
    prompt = (
        f"You are a traffic reporter for Bellevue, WA and the Eastside commute region.\n"
        f"Here are {len(impactful)} key road alerts currently affecting Bellevue highway corridors{timing_clause}:\n\n"
        f"{chr(10).join(alert_lines)}\n\n"
        f"Provide a concise commute briefing in Markdown with these EXACT 3 sections:\n"
        f"### 🏙️ Bellevue & Eastside Commuter Briefing\n\n"
        f"#### 🚨 Critical Disruptions (TL;DR)\n"
        f"- 2 short bullets on the highest-impact closures or ramp blocks.\n\n"
        f"#### 🛣️ Key Corridor Impacts\n"
        f"- 1-2 sentences on I-405, SR 520, and I-90 corridor outlook.\n\n"
        f"#### 💡 Commuter Advice & Detours\n"
        f"- 1 practical tip for Bellevue drivers.\n\n"
        f"Keep the entire briefing under 150 words. Be direct and driver-focused."
    )

    _log(log_path, f"PRE-FLIGHT (WSDOT): Requesting LLM commute analysis for {len(impactful)} alerts...\n")

    try:
        async with httpx.AsyncClient(timeout=float(getattr(config, "LLM_TIMEOUT", 90))) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a professional Bellevue traffic reporter. Output ONLY the final Markdown briefing."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 800
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                if "Bellevue" in content:
                    _log(log_path, "PRE-FLIGHT (WSDOT): LLM commute briefing generated successfully.\n")
                    return content
    except Exception as e:
        logger.warning(f"[wsdot] LLM commute briefing fallback ({e}); using deterministic briefing.")
        _log(log_path, f"PRE-FLIGHT (WSDOT): LLM briefing fallback due to: {e}\n")

    return generate_deterministic_bellevue_briefing(impactful, len(bellevue_alerts), timewindow_label=timewindow_label)


# ==========================================
# 6. Data Retrieval & Formatting
# ==========================================

async def fetch_wsdot_highway_alerts(access_code: Optional[str] = None, log_path: str = "") -> List[Dict[str, Any]]:
    """Fetch raw Highway Alerts from WSDOT REST API."""
    code = access_code or os.getenv("WSDOT_ACCESS_CODE")
    if not code:
        err_msg = "WSDOT API Error: WSDOT_ACCESS_CODE not found in environment or .env file."
        logger.error(f"[wsdot] {err_msg}")
        _log(log_path, f"WSDOT ERROR: {err_msg}\n")
        return []

    url = WSDOT_ALERTS_API_URL.format(access_code=code)
    _log(log_path, f"WSDOT API: Requesting highway alerts from {url.split('?')[0]}...\n")

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    _log(log_path, f"WSDOT API: Successfully fetched {len(data)} statewide alerts.\n")
                    return data
            logger.error(f"[wsdot] API HTTP error {resp.status_code}: {resp.text[:200]}")
            _log(log_path, f"WSDOT API HTTP {resp.status_code} Error: {resp.text[:200]}\n")
    except Exception as e:
        logger.error(f"[wsdot] Network / Fetch error: {e}")
        _log(log_path, f"WSDOT API Fetch Exception: {e}\n")
    return []


def format_alerts_report(
    king_alerts: List[Dict[str, Any]], 
    total_statewide: int, 
    llm_briefing: Optional[str] = None,
    timewindow_label: Optional[str] = None,
    total_king_unfiltered: Optional[int] = None
) -> str:
    """Formats King County highway alerts into a structured, readable Markdown report with Eastside LLM briefing."""
    if not king_alerts:
        period_text = f" matching **{timewindow_label}**" if timewindow_label else ""
        return (
            "# 🚗 WSDOT Highway Alerts: King County Area\n\n"
            f"✅ **No active highway alerts or major traffic disruptions reported in King County{period_text}.**\n\n"
            "***\n"
            f"📊 **SUMMARY:** 0 alerts found in King County out of {total_statewide} statewide alerts."
        )

    def sort_key(item):
        prio = (item.get("Priority") or "LOW").upper()
        prio_rank = PRIORITY_ORDER.get(prio, 99)
        road = (item.get("StartRoadwayLocation") or {}).get("RoadName") or "999"
        return (prio_rank, road)

    sorted_alerts = sorted(king_alerts, key=sort_key)
    counts = {"Highest": 0, "High": 0, "Medium": 0, "Low": 0, "Lowest": 0}
    grouped_by_road: Dict[str, List[Dict[str, Any]]] = {}

    for a in sorted_alerts:
        p = (a.get("Priority") or "Low").capitalize()
        counts[p] = counts.get(p, 0) + 1

        road_raw = (a.get("StartRoadwayLocation") or {}).get("RoadName") or (a.get("EndRoadwayLocation") or {}).get("RoadName")
        hwy_label = format_highway_name(road_raw)
        grouped_by_road.setdefault(hwy_label, []).append(a)

    high_prio_total = counts.get("Highest", 0) + counts.get("High", 0)

    md = ["# 🚗 WSDOT Highway Alerts: King County Area\n"]
    if timewindow_label:
        md.append(f"📅 **Target Period:** {timewindow_label}\n")

    if llm_briefing:
        md.append(llm_briefing.strip() + "\n")
        md.append("***\n")

    filter_note = f" ({total_king_unfiltered} total alerts across all dates in King County)" if total_king_unfiltered and total_king_unfiltered != len(king_alerts) else ""
    md.append(f"**Found {len(king_alerts)} active alerts** affecting highways in King County during this period{filter_note} ({total_statewide} alerts statewide).\n")

    # Critical Alerts Callout
    critical = [a for a in sorted_alerts if (a.get("Priority") or "").upper() in ("HIGHEST", "HIGH")]
    if critical:
        md.append("### 🚨 High Priority & Severe Disruptions")
        for a in critical:
            hwy = format_highway_name((a.get("StartRoadwayLocation") or {}).get("RoadName"))
            cat = a.get("EventCategory") or "Incident"
            headline = a.get("HeadlineDescription") or "No description provided."
            md.append(f"- **🚨 {hwy} — {cat.upper()}**")
            md.append(f"  - **Alert:** {headline}")
            md.append(f"  - **Duration:** {parse_wsdot_date(a.get('StartTime'))} ➔ {parse_wsdot_date(a.get('EndTime'))}")
        md.append("")

    # Corridor Breakdown
    md.append("### 🛣️ Alerts by Highway Corridor\n")
    corridor_ranks = {"I-405": 1, "SR 520": 2, "I-90": 3, "I-5": 4, "SR 522": 5, "SR 18": 6, "SR 167": 7, "SR 99": 8}

    for corridor in sorted(grouped_by_road.keys(), key=lambda c: corridor_ranks.get(c, 50)):
        items = grouped_by_road[corridor]
        md.append(f"#### **{corridor} ({len(items)} alert{'s' if len(items) > 1 else ''})**")

        for idx, a in enumerate(items, 1):
            category = a.get("EventCategory") or "Alert"
            priority = (a.get("Priority") or "Medium").capitalize()
            prio_badge = "🚨" if priority in ("Highest", "High") else "⚠️" if priority == "Medium" else "ℹ️"

            start_loc = a.get("StartRoadwayLocation") or {}
            end_loc = a.get("EndRoadwayLocation") or {}
            dir_code = start_loc.get("Direction") or end_loc.get("Direction") or ""
            direction = DIRECTION_MAP.get(dir_code, dir_code)

            mp_start, mp_end = start_loc.get("MilePost"), end_loc.get("MilePost")
            mp_str = f"MP {mp_start}" if mp_start is not None else ""
            if mp_end is not None and mp_end != mp_start:
                mp_str += f" - {mp_end}"

            loc_parts = [p for p in (direction, mp_str) if p]
            loc_line = f" ({', '.join(loc_parts)})" if loc_parts else ""

            headline = (a.get("HeadlineDescription") or "").strip()
            extended = (a.get("ExtendedDescription") or "").strip()

            md.append(f"{idx}. {prio_badge} **[{category}]**{loc_line} — *Priority: {priority}*")
            md.append(f"   * **Details:** {headline}")
            if extended and extended != headline and len(extended) > 10:
                md.append(f"   * **Notes:** {extended}")
            md.append(f"   * **Schedule:** {parse_wsdot_date(a.get('StartTime'))} to {parse_wsdot_date(a.get('EndTime'))}")
            md.append("")

    md.append("***")
    md.append(
        f"📊 **SUMMARY:** {len(king_alerts)} alerts found in King County "
        f"({high_prio_total} High/Highest, {counts.get('Medium', 0)} Medium, {counts.get('Low', 0) + counts.get('Lowest', 0)} Low) "
        f"out of {total_statewide} statewide alerts."
    )
    return "\n".join(md)


# ==========================================
# 7. Pre-Flight Orchestrator & Plugin
# ==========================================

async def wsdot_run_pre_flight(
    browser: Optional[BrowserSession] = None,
    prompt: str = "",
    context_str: str = "",
    log_path: str = "",
    llm: Any = None
) -> str:
    """Pre-flight execution logic for WSDOT Highway Alerts."""
    _log(log_path, "PRE-FLIGHT (WSDOT): Starting Washington State Highway Alerts retrieval...\n")

    all_alerts = await fetch_wsdot_highway_alerts(log_path=log_path)
    if not all_alerts:
        return (
            "# 🚗 WSDOT Highway Alerts: King County Area\n\n"
            "❌ **Unable to retrieve highway alerts from WSDOT API.** "
            "Please verify your `WSDOT_ACCESS_CODE` in `.env` and network connectivity.\n\n"
            "***\n"
            "📊 **SUMMARY:** 0 alerts found in King County (API error or empty response)."
        )

    all_king_alerts = [a for a in all_alerts if is_in_king_county(a)]
    query = f"{prompt} {context_str}".strip()
    window_start, window_end, window_label = await resolve_timewindow(query, log_path=log_path)

    active_king_alerts = (
        [a for a in all_king_alerts if alert_overlaps_timewindow(a, window_start, window_end)]
        if window_start and window_end else all_king_alerts
    )

    bellevue_alerts = [a for a in active_king_alerts if is_bellevue_eastside_alert(a)]
    _log(log_path, f"PRE-FLIGHT (WSDOT): Found {len(active_king_alerts)} alerts in King County ({len(bellevue_alerts)} Bellevue/Eastside) for {window_label}.\n")

    briefing = await generate_llm_commute_briefing(bellevue_alerts, timewindow_label=window_label, log_path=log_path)
    report_md = format_alerts_report(
        active_king_alerts,
        total_statewide=len(all_alerts),
        llm_briefing=briefing,
        timewindow_label=window_label,
        total_king_unfiltered=len(all_king_alerts)
    )

    _log(log_path, f"PRE-FLIGHT SUCCESS: WSDOT report generated with {len(active_king_alerts)} King County alerts for {window_label}.\n")
    return report_md


class WsdotPlugin(BaseSitePlugin):
    description = "Washington State DOT highway traffic, road alerts, and pass reports."

    def register_actions(self, controller: Any) -> None:
        controller.action('wsdot_get_highway_alerts')(wsdot_get_highway_alerts)

    async def run_pre_flight(
        self, browser: Optional[BrowserSession], prompt: str, context: str, log_path: str = "", llm: Any = None
    ) -> str:
        return await wsdot_run_pre_flight(browser, prompt, context, log_path, llm)

    def evaluate_outcome(self, result_text: str) -> Optional[str]:
        lower_res = result_text.lower()
        wsdot_match = re.search(r'(\d+)\s*alerts?\s*(?:found\s*in|in)\s*([a-z\s]+?)\s*.*?out\s*of\s*(\d+)\s*statewide', lower_res)
        if wsdot_match:
            alerts_found = int(wsdot_match.group(1))
            return "SUCCESS" if alerts_found > 0 else "NO_RESULT"
        if "0 alerts found in king county" in lower_res or "no active highway alerts" in lower_res:
            return "NO_RESULT"
        return None


async def wsdot_get_highway_alerts(area: str = "King County", timewindow: str = "") -> str:
    """SITE-SPECIFIC SKILL: Retrieves current active highway alerts for King County from WSDOT API."""
    return await wsdot_run_pre_flight(browser=None, prompt=f"{area} {timewindow}".strip())
