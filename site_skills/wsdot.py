# site_skills/wsdot.py
import logging
import os
import re
import json
import asyncio
from datetime import datetime, timedelta
from typing import Any, List, Dict, Optional
import httpx
from dotenv import load_dotenv

from backend.core.plugin import BaseSitePlugin
from browser_use import BrowserSession
from backend import config

load_dotenv()
logger = logging.getLogger(__name__)

# WSDOT REST API Endpoint
WSDOT_ALERTS_API_URL = "http://wsdot.wa.gov/Traffic/api/HighwayAlerts/HighwayAlertsREST.svc/GetAlertsAsJson?AccessCode={access_code}"

# King County Geographic Bounding Box (Lat/Lon)
# South: ~47.15 (Pierce border), North: ~47.78 (Snohomish border)
# West: ~-122.53 (Vashon/Puget Sound), East: ~-121.12 (Cascade Crest / Stevens Pass)
KING_COUNTY_LAT_MIN = 47.15
KING_COUNTY_LAT_MAX = 47.78
KING_COUNTY_LON_MIN = -122.53
KING_COUNTY_LON_MAX = -121.12

# Common King County municipalities, unincorporated communities, and landmarks
KING_COUNTY_CITIES = [
    "seattle", "bellevue", "redmond", "kirkland", "renton", "kent", "federal way",
    "auburn", "shoreline", "burien", "bothell", "snoqualmie", "issaquah",
    "mercer island", "tukwila", "seatac", "kenmore", "woodinville", "maple valley",
    "sammamish", "north bend", "des moines", "covington", "enumclaw", "skykomish",
    "vashon", "white center", "newcastle", "fall city", "carnation", "duvall",
    "black diamond", "algona", "pacific", "milton", "normandy park", "lake forest park"
]

# Municipalities and communities specifically defining Bellevue and Eastside commute corridors
BELLEVUE_EASTSIDE_CITIES = [
    "bellevue", "redmond", "kirkland", "issaquah", "mercer island",
    "renton", "newcastle", "sammamish", "bothell", "woodinville",
    "factoria", "overlake", "eastgate", "medina", "clyde hill",
    "newport", "totem lake", "kenmore", "cougar mountain"
]


INTERSTATE_MAP = {
    "005": "I-5", "5": "I-5",
    "090": "I-90", "90": "I-90",
    "405": "I-405",
    "082": "I-82", "82": "I-82",
    "182": "I-182",
    "205": "I-205",
    "705": "I-705"
}

US_HIGHWAY_MAP = {
    "002": "US 2", "2": "US 2",
    "012": "US 12", "12": "US 12",
    "097": "US 97", "97": "US 97",
    "101": "US 101",
    "195": "US 195",
    "395": "US 395"
}

DIRECTION_MAP = {
    "N": "Northbound", "NB": "Northbound",
    "S": "Southbound", "SB": "Southbound",
    "E": "Eastbound", "EB": "Eastbound",
    "W": "Westbound", "WB": "Westbound",
    "B": "Both Directions"
}

PRIORITY_ORDER = {
    "HIGHEST": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "LOWEST": 4
}

def format_highway_name(road_name: Optional[str]) -> str:
    """Format raw WSDOT RoadName (e.g., '005', '405', '090', '018') into readable Highway designation."""
    if not road_name:
        return "Local / Highway"
    cleaned = str(road_name).strip()
    if cleaned in INTERSTATE_MAP:
        return INTERSTATE_MAP[cleaned]
    if cleaned in US_HIGHWAY_MAP:
        return US_HIGHWAY_MAP[cleaned]
    
    # State Routes: strip leading zeros if numeric
    digits = re.sub(r'^0+', '', cleaned)
    if digits.isdigit():
        return f"SR {digits}"
    return f"SR {cleaned}"

def parse_wsdot_date(date_str: Optional[str]) -> str:
    """Parse Microsoft JSON date format e.g. /Date(1791576540000-0700)/ into human-readable string."""
    if not date_str:
        return "Ongoing / Until Further Notice"
    
    match = re.search(r'/Date\((\d+)(?:([+-]\d{4}))?\)/', str(date_str))
    if match:
        timestamp_ms = int(match.group(1))
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.strftime("%b %d, %Y, %I:%M %p")
    return str(date_str)

def parse_wsdot_datetime(date_str: Optional[str]) -> Optional[datetime]:
    """Parse Microsoft JSON date format e.g. /Date(1791576540000-0700)/ into datetime object."""
    if not date_str:
        return None
    match = re.search(r'/Date\((\d+)', str(date_str))
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000)
    return None

def alert_overlaps_timewindow(alert: Dict[str, Any], window_start: datetime, window_end: datetime) -> bool:
    """Determines whether a highway alert is active or overlaps with the [window_start, window_end] interval.
    
    An alert is relevant if it started on or before the end of the window,
    and has not ended before the start of the window (None = ongoing / until further notice).
    """
    a_start = parse_wsdot_datetime(alert.get("StartTime")) or datetime.min
    a_end = parse_wsdot_datetime(alert.get("EndTime"))
    return a_start <= window_end and (a_end is None or a_end >= window_start)

MONTH_MAP = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'september': 9, 'oct': 10, 'october': 10,
    'nov': 11, 'november': 11, 'dec': 12, 'december': 12
}

WEEKDAY_MAP = {
    'monday': 0, 'mon': 0, 'tuesday': 1, 'tue': 1, 'wednesday': 2, 'wed': 2,
    'thursday': 3, 'thu': 3, 'friday': 4, 'fri': 4, 'saturday': 5, 'sat': 5,
    'sunday': 6, 'sun': 6
}

DATE_RANGE_REGEX = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*(?:to|-|through)\s*(?:(jan[a-z]*|feb[a-z]*|mar[a-z]*|apr[a-z]*|may[a-z]*|jun[a-z]*|jul[a-z]*|aug[a-z]*|sep[a-z]*|oct[a-z]*|nov[a-z]*|dec[a-z]*)\s+)?(\d{1,2})(?:st|nd|rd|th)?)?(?:,?\s*(\d{4}))?\b',
    re.IGNORECASE
)

async def resolve_timewindow_with_llm(query: str, now: datetime, log_path: str = "") -> Optional[tuple[datetime, datetime, str]]:
    """Fallback: Uses the local LLM to extract an arbitrary or complex time window into ISO datetime start/end."""
    llm_base = config.LLM_BASE_URL.rstrip('/')
    llm_model = config.LLM_MODEL
    
    prompt = (
        f"Current local date and time: {now.strftime('%Y-%m-%d %H:%M:%S (%A)')}.\n"
        f"User query regarding traffic timing: \"{query}\"\n\n"
        f"Extract the requested date/time window as valid JSON matching this schema:\n"
        f"{{\n"
        f"  \"start\": \"YYYY-MM-DDTHH:MM:SS\",\n"
        f"  \"end\": \"YYYY-MM-DDTHH:MM:SS\",\n"
        f"  \"label\": \"Short human-readable period description\"\n"
        f"}}\n"
        f"Respond with JSON ONLY."
    )
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{llm_base}/chat/completions",
                json={
                    "model": llm_model,
                    "messages": [
                        {"role": "system", "content": "You are a date and time extraction assistant. Output strictly valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 150
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                content = (msg.get("content") or "").strip()
                if not content and "reasoning_content" in msg:
                    content = msg["reasoning_content"].strip()
                
                json_match = re.search(r'\{.*?\}', content, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    start_dt = datetime.fromisoformat(parsed["start"])
                    end_dt = datetime.fromisoformat(parsed["end"])
                    label = parsed.get("label", f"{start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d')}")
                    return start_dt, end_dt, label
    except Exception as e:
        logger.debug(f"[wsdot] LLM timewindow resolution skipped: {e}")
        
    return None

async def resolve_timewindow(query_str: str, base_time: Optional[datetime] = None, log_path: str = "") -> tuple[Optional[datetime], Optional[datetime], str]:
    """Resolves user prompt or parameter into a concrete (start_datetime, end_datetime, label) interval.
    
    Returns (None, None, 'Current Active Alerts') if no specific time window is requested.
    """
    now = base_time or datetime.now()
    q = (query_str or "").lower().strip()
    if not q:
        return None, None, "Current Active Alerts"

    # 1. Weekend Window (covers Friday evening 6 PM through Monday morning 5 AM)
    if any(w in q for w in ["following weekend", "next weekend", "this weekend", "upcoming weekend", "weekend"]):
        if now.weekday() == 4:  # Friday
            start = now.replace(hour=18, minute=0, second=0, microsecond=0)
            end = (now + timedelta(days=3)).replace(hour=5, minute=0, second=0, microsecond=0)
        elif now.weekday() in (5, 6):  # Saturday or Sunday
            if any(w in q for w in ["following weekend", "next weekend"]):
                days = (4 - now.weekday()) % 7 + 7
                fri = now + timedelta(days=days)
                start = fri.replace(hour=18, minute=0, second=0, microsecond=0)
                end = (fri + timedelta(days=3)).replace(hour=5, minute=0, second=0, microsecond=0)
            else:
                days_back = now.weekday() - 4
                fri = now - timedelta(days=days_back)
                start = fri.replace(hour=18, minute=0, second=0, microsecond=0)
                end = (fri + timedelta(days=3)).replace(hour=5, minute=0, second=0, microsecond=0)
        else:  # Monday through Thursday
            days = (4 - now.weekday()) % 7
            fri = now + timedelta(days=days)
            start = fri.replace(hour=18, minute=0, second=0, microsecond=0)
            end = (fri + timedelta(days=3)).replace(hour=5, minute=0, second=0, microsecond=0)
            
        label = f"Upcoming Weekend ({start.strftime('%b %d')} evening – {end.strftime('%b %d')} morning)"
        return start, end, label

    # 2. Tomorrow
    if "tomorrow" in q:
        tmrw = now + timedelta(days=1)
        start = tmrw.replace(hour=0, minute=0, second=0, microsecond=0)
        end = tmrw.replace(hour=23, minute=59, second=59, microsecond=0)
        return start, end, f"Tomorrow ({tmrw.strftime('%b %d, %Y')})"

    # 3. Today
    if "today" in q or "tonight" in q:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return start, end, f"Today ({now.strftime('%b %d, %Y')})"

    # 4. Specific Weekday (e.g. Saturday, Sunday, Friday)
    for day_name, day_idx in WEEKDAY_MAP.items():
        if re.search(rf"\b{day_name}\b", q):
            days_ahead = (day_idx - now.weekday()) % 7
            if days_ahead == 0 and "next" in q:
                days_ahead = 7
            target = now + timedelta(days=days_ahead)
            start = target.replace(hour=0, minute=0, second=0, microsecond=0)
            end = target.replace(hour=23, minute=59, second=59, microsecond=0)
            return start, end, target.strftime("%A, %b %d, %Y")

    # 5. Explicit Month / Day pattern (e.g. 'Oct 17', 'Oct 17 - 19')
    match = DATE_RANGE_REGEX.search(q)
    if match:
        m1_name, d1_str, m2_name, d2_str, year_str = match.groups()
        m1 = MONTH_MAP.get(m1_name[:3].lower(), now.month)
        d1 = int(d1_str)
        year = int(year_str) if year_str else now.year
        
        start = datetime(year, m1, d1, 0, 0, 0)
        if d2_str:
            m2 = MONTH_MAP.get(m2_name[:3].lower(), m1) if m2_name else m1
            d2 = int(d2_str)
            end = datetime(year, m2, d2, 23, 59, 59)
            label = f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
        else:
            end = datetime(year, m1, d1, 23, 59, 59)
            label = start.strftime("%b %d, %Y")
        return start, end, label

    # 6. Fallback: LLM extraction for complex natural language queries
    time_keywords = ["day", "week", "month", "night", "morning", "after", "before", "between", "game", "holiday"]
    if any(k in q for k in time_keywords):
        llm_res = await resolve_timewindow_with_llm(q, now=now, log_path=log_path)
        if llm_res:
            return llm_res

    return None, None, "Current Active Alerts"


def is_in_king_county(item: Dict[str, Any]) -> bool:
    """Determines whether a highway alert is located in or affects King County."""
    # 1. Direct County field check
    county = item.get("County")
    if county and "king" in str(county).lower():
        return True

    # 2. Roadway coordinate bounding box check (Start or End location)
    for loc_key in ("StartRoadwayLocation", "EndRoadwayLocation"):
        loc = item.get(loc_key)
        if isinstance(loc, dict):
            lat = loc.get("Latitude")
            lon = loc.get("Longitude")
            if lat is not None and lon is not None:
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                    if (KING_COUNTY_LAT_MIN <= lat_f <= KING_COUNTY_LAT_MAX and 
                        KING_COUNTY_LON_MIN <= lon_f <= KING_COUNTY_LON_MAX):
                        return True
                except (ValueError, TypeError):
                    pass

    # 3. Municipality / Landmark text mention check
    headline = (item.get("HeadlineDescription") or "").lower()
    extended = (item.get("ExtendedDescription") or "").lower()
    full_text = f"{headline} {extended}"

    if "king county" in full_text:
        return True

    for city in KING_COUNTY_CITIES:
        # Check whole word match for city name
        if re.search(rf"\b{re.escape(city)}\b", full_text):
            return True

    return False

def is_bellevue_eastside_alert(item: Dict[str, Any]) -> bool:
    """Determines whether a highway alert impacts Bellevue or the surrounding Eastside commute area."""
    # 1. Text search in headline and extended description
    headline = (item.get("HeadlineDescription") or "").lower()
    extended = (item.get("ExtendedDescription") or "").lower()
    full_text = f"{headline} {extended}"

    for city in BELLEVUE_EASTSIDE_CITIES:
        if re.search(rf"\b{re.escape(city)}\b", full_text):
            return True

    # 2. Key Eastside highways (I-405, SR 520, I-90)
    for loc_key in ("StartRoadwayLocation", "EndRoadwayLocation"):
        loc = item.get(loc_key)
        if isinstance(loc, dict):
            road = str(loc.get("RoadName") or "").strip()
            lat = loc.get("Latitude")
            lon = loc.get("Longitude")
            
            # Check coordinates if present
            if lat is not None and lon is not None:
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                    # I-405, SR 520, SR 522 corridors in King County Eastside
                    if road in ("405", "520", "522") and (47.45 <= lat_f <= 47.80 and -122.35 <= lon_f <= -122.00):
                        return True
                    # I-90 corridor Eastside portion (Mercer Island to Issaquah)
                    if road in ("090", "90") and (47.50 <= lat_f <= 47.62 and -122.30 <= lon_f <= -122.00):
                        return True
                    # General Eastside geographic bounding box
                    if 47.50 <= lat_f <= 47.72 and -122.25 <= lon_f <= -122.05:
                        return True
                except (ValueError, TypeError):
                    pass
            elif road in ("405", "520"):
                # I-405 and SR 520 are primarily Eastside corridors
                return True

    return False

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
        if "i-405" in h or "405" in h or "520" in h or "i-90" in h or "90" in h: score += 15
        return score

    ranked = sorted(alerts, key=score_alert, reverse=True)
    return ranked[:max_items]

def generate_deterministic_bellevue_briefing(impactful: List[Dict[str, Any]], total_bellevue: int, timewindow_label: str = "") -> str:
    """Fallback deterministic briefing for Bellevue and Eastside commuters."""
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
        start = parse_wsdot_date(a.get("StartTime"))
        end = parse_wsdot_date(a.get("EndTime"))
        lines.append(f"- **{hwy}:** {h} *(Scheduled: {start} ➔ {end})*")

    lines.append("")
    lines.append("#### 💡 Commuter Advice")
    lines.append("- Allow additional travel time on I-405 and connecting ramps; monitor real-time navigation for unexpected slowdowns.")

    return "\n".join(lines)

async def generate_llm_commute_briefing(bellevue_alerts: List[Dict[str, Any]], timewindow_label: str = "", log_path: str = "") -> str:
    """Leverages the local LLM to generate an executive commute briefing for Bellevue and Eastside drivers."""
    total_bellevue = len(bellevue_alerts)
    impactful = select_top_impactful_alerts(bellevue_alerts, max_items=4)

    if not impactful:
        return generate_deterministic_bellevue_briefing([], total_bellevue=0, timewindow_label=timewindow_label)

    # Prepare condensed alert descriptions for prompt
    alert_lines = []
    for idx, a in enumerate(impactful, 1):
        road_raw = (a.get("StartRoadwayLocation") or {}).get("RoadName")
        hwy = format_highway_name(road_raw)
        prio = (a.get("Priority") or "Medium").capitalize()
        headline = (a.get("HeadlineDescription") or "").strip()
        start = parse_wsdot_date(a.get("StartTime"))
        end = parse_wsdot_date(a.get("EndTime"))
        alert_lines.append(f"{idx}. [{hwy} - {prio}] {headline} (Schedule: {start} to {end})")

    alerts_text = "\n".join(alert_lines)
    timing_clause = f" active during **{timewindow_label}**" if timewindow_label else ""

    prompt = (
        f"You are a traffic reporter for Bellevue, WA and the Eastside commute region.\n"
        f"Here are {len(impactful)} key road alerts currently affecting Bellevue highway corridors{timing_clause}:\n\n"
        f"{alerts_text}\n\n"
        f"Provide a concise commute briefing in Markdown with these EXACT 3 sections:\n"
        f"### 🏙️ Bellevue & Eastside Commuter Briefing\n\n"
        f"#### 🚨 Critical Disruptions (TL;DR)\n"
        f"- 2 short bullets on the highest-impact closures or ramp blocks during this period.\n\n"
        f"#### 🛣️ Key Corridor Impacts\n"
        f"- 1-2 sentences on I-405, SR 520, and I-90 corridor outlook.\n\n"
        f"#### 💡 Commuter Advice & Detours\n"
        f"- 1 practical tip for Bellevue drivers.\n\n"
        f"Keep the entire briefing under 150 words. Be direct and driver-focused."
    )

    llm_base = config.LLM_BASE_URL.rstrip('/')
    llm_model = config.LLM_MODEL
    timeout_secs = max(90.0, float(getattr(config, "LLM_TIMEOUT", 90)))

    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (WSDOT): Requesting LLM commute analysis for {len(impactful)} top Bellevue alerts from {llm_model}...\n")

    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            resp = await client.post(
                f"{llm_base}/chat/completions",
                json={
                    "model": llm_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a professional Bellevue traffic reporter. Provide ONLY the final Markdown briefing without any thinking scratchpad or preamble."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 800
                }
            )

            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                content = (msg.get("content") or "").strip()
                if not content and "reasoning_content" in msg:
                    content = msg["reasoning_content"].strip()

                # Clean up any leftover chain-of-thought tags or preamble
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                content = re.sub(r'^Thinking Process:.*?\n\n', '', content, flags=re.DOTALL).strip()

                # Slice from where the actual briefing begins, stripping any thinking scratchpad
                crit_matches = list(re.finditer(r'####\s*🚨?\s*Critical Disruptions', content, re.IGNORECASE))
                header_marker = "### 🏙️ Bellevue & Eastside Commuter Briefing"
                if crit_matches:
                    content = f"{header_marker}\n\n" + content[crit_matches[-1].start():].strip()
                elif header_marker in content:
                    content = content[content.rfind(header_marker):].strip()
                elif "###" in content and "Bellevue" in content:
                    content = content[content.rfind("###"):].strip()

                if content and "Bellevue" in content:
                    logger.info("[wsdot] LLM commute briefing generated successfully.")
                    if log_path:
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write("PRE-FLIGHT (WSDOT): LLM commute briefing generated successfully.\n")
                    return content
            else:
                logger.warning(f"[wsdot] LLM endpoint returned status {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"[wsdot] LLM commute briefing fallback ({type(e).__name__}: {e}); using deterministic briefing.")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT (WSDOT): LLM briefing fallback due to: {type(e).__name__} ({e})\n")

    # Graceful deterministic fallback
    return generate_deterministic_bellevue_briefing(impactful, total_bellevue, timewindow_label=timewindow_label)

async def fetch_wsdot_highway_alerts(access_code: Optional[str] = None, log_path: str = "") -> List[Dict[str, Any]]:
    """Fetch raw Highway Alerts from WSDOT REST API."""
    code = access_code or os.getenv("WSDOT_ACCESS_CODE")
    if not code:
        err_msg = "WSDOT API Error: WSDOT_ACCESS_CODE not found in environment or .env file."
        logger.error(f"[wsdot] {err_msg}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"WSDOT ERROR: {err_msg}\n")
        return []

    url = WSDOT_ALERTS_API_URL.format(access_code=code)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"WSDOT API: Requesting highway alerts from {url.split('?')[0]}...\n")

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.error(f"[wsdot] API HTTP error {resp.status_code}: {resp.text[:200]}")
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"WSDOT API HTTP {resp.status_code} Error: {resp.text[:200]}\n")
                return []

            data = resp.json()
            if isinstance(data, list):
                logger.info(f"[wsdot] Successfully fetched {len(data)} total statewide alerts.")
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"WSDOT API: Successfully fetched {len(data)} statewide alerts.\n")
                return data
            else:
                logger.warning(f"[wsdot] Unexpected API response format: {type(data)}")
                return []
    except Exception as e:
        logger.error(f"[wsdot] Network / Fetch error: {e}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"WSDOT API Fetch Exception: {e}\n")
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

    # Sort alerts by Priority (Highest -> Lowest) and then by RoadName
    def sort_key(item):
        prio = (item.get("Priority") or "LOW").upper()
        prio_rank = PRIORITY_ORDER.get(prio, 99)
        road = (item.get("StartRoadwayLocation") or {}).get("RoadName") or "999"
        return (prio_rank, road)

    sorted_alerts = sorted(king_alerts, key=sort_key)

    # Count by priority
    counts = {"Highest": 0, "High": 0, "Medium": 0, "Low": 0, "Lowest": 0}
    for a in sorted_alerts:
        p = (a.get("Priority") or "Low").capitalize()
        counts[p] = counts.get(p, 0) + 1

    high_priority_total = counts.get("Highest", 0) + counts.get("High", 0)

    # Group by Highway / Corridor
    grouped_by_road: Dict[str, List[Dict[str, Any]]] = {}
    for a in sorted_alerts:
        road_raw = (a.get("StartRoadwayLocation") or {}).get("RoadName") or (a.get("EndRoadwayLocation") or {}).get("RoadName")
        hwy_label = format_highway_name(road_raw)
        if hwy_label not in grouped_by_road:
            grouped_by_road[hwy_label] = []
        grouped_by_road[hwy_label].append(a)

    md = ["# 🚗 WSDOT Highway Alerts: King County Area\n"]

    # 1. Target Time Window Badge
    if timewindow_label:
        md.append(f"📅 **Target Period:** {timewindow_label}\n")

    # 2. Executive Bellevue / Eastside Commuter Briefing (if generated)
    if llm_briefing:
        md.append(llm_briefing.strip() + "\n")
        md.append("***\n")

    filter_note = f" ({total_king_unfiltered} total alerts across all dates in King County)" if total_king_unfiltered and total_king_unfiltered != len(king_alerts) else ""
    md.append(f"**Found {len(king_alerts)} active alerts** affecting highways in King County during this period{filter_note} ({total_statewide} alerts statewide).\n")

    # 3. Critical Alerts Callout
    critical_alerts = [a for a in sorted_alerts if (a.get("Priority") or "").upper() in ("HIGHEST", "HIGH")]
    if critical_alerts:
        md.append("### 🚨 High Priority & Severe Disruptions")
        for a in critical_alerts:
            road_raw = (a.get("StartRoadwayLocation") or {}).get("RoadName")
            hwy = format_highway_name(road_raw)
            cat = a.get("EventCategory") or "Incident"
            headline = a.get("HeadlineDescription") or "No description provided."
            start_str = parse_wsdot_date(a.get("StartTime"))
            end_str = parse_wsdot_date(a.get("EndTime"))
            
            md.append(f"- **🚨 {hwy} — {cat.upper()}**")
            md.append(f"  - **Alert:** {headline}")
            md.append(f"  - **Duration:** {start_str} ➔ {end_str}")
        md.append("")

    # 4. Corridor Breakdown
    md.append("### 🛣️ Alerts by Highway Corridor\n")

    # Sort corridors: major interstates first (I-405, I-5, I-90), then SRs
    def corridor_sort_key(c_name: str):
        order = {"I-405": 1, "SR 520": 2, "I-90": 3, "I-5": 4, "SR 522": 5, "SR 18": 6, "SR 167": 7, "SR 99": 8}
        return order.get(c_name, 50)

    for corridor in sorted(grouped_by_road.keys(), key=corridor_sort_key):
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
            
            mp_start = start_loc.get("MilePost")
            mp_end = end_loc.get("MilePost")
            mp_str = f"MP {mp_start}" if mp_start is not None else ""
            if mp_end is not None and mp_end != mp_start:
                mp_str += f" - {mp_end}"

            headline = (a.get("HeadlineDescription") or "").strip()
            extended = (a.get("ExtendedDescription") or "").strip()
            
            start_time = parse_wsdot_date(a.get("StartTime"))
            end_time = parse_wsdot_date(a.get("EndTime"))

            loc_details = []
            if direction:
                loc_details.append(direction)
            if mp_str:
                loc_details.append(mp_str)
            loc_line = f" ({', '.join(loc_details)})" if loc_details else ""

            md.append(f"{idx}. {prio_badge} **[{category}]**{loc_line} — *Priority: {priority}*")
            md.append(f"   * **Details:** {headline}")
            if extended and extended != headline and len(extended) > 10:
                md.append(f"   * **Notes:** {extended}")
            md.append(f"   * **Schedule:** {start_time} to {end_time}")
            md.append("")

    md.append("***")
    md.append(
        f"📊 **SUMMARY:** {len(king_alerts)} alerts found in King County "
        f"({high_priority_total} High/Highest, {counts.get('Medium', 0)} Medium, {counts.get('Low', 0) + counts.get('Lowest', 0)} Low) "
        f"out of {total_statewide} statewide alerts."
    )

    return "\n".join(md)

async def wsdot_run_pre_flight(browser: BrowserSession, prompt: str, context_str: str = "", log_path: str = "", llm: Any = None) -> str:
    """SITE-SPECIFIC PRE-FLIGHT: Fetches WSDOT highway alerts via REST API,
    resolves any time window (e.g. weekend, specific date), filters King County
    and Eastside/Bellevue alerts, generates an LLM commuter briefing, and produces a structured report.
    """
    logger.info("[wsdot_run_pre_flight] Starting WSDOT Highway Alerts check for King County area.")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("PRE-FLIGHT (WSDOT): Starting Washington State Highway Alerts retrieval...\n")

    # 1. Fetch raw alerts from WSDOT REST API
    all_alerts = await fetch_wsdot_highway_alerts(log_path=log_path)
    if not all_alerts:
        err_report = (
            "# 🚗 WSDOT Highway Alerts: King County Area\n\n"
            "❌ **Unable to retrieve highway alerts from WSDOT API.** "
            "Please verify your `WSDOT_ACCESS_CODE` in `.env` and network connectivity.\n\n"
            "***\n"
            "📊 **SUMMARY:** 0 alerts found in King County (API error or empty response)."
        )
        return err_report

    # 2. Filter for all alerts located in King County
    all_king_alerts = [alert for alert in all_alerts if is_in_king_county(alert)]
    logger.info(f"[wsdot_run_pre_flight] Identified {len(all_king_alerts)} total King County alerts out of {len(all_alerts)} statewide.")

    # 3. Resolve Target Time Window from prompt & context
    combined_query = f"{prompt} {context_str}".strip()
    window_start, window_end, window_label = await resolve_timewindow(combined_query, log_path=log_path)

    # 4. Filter alerts active/overlapping target time window
    if window_start and window_end:
        active_king_alerts = [a for a in all_king_alerts if alert_overlaps_timewindow(a, window_start, window_end)]
        logger.info(f"[wsdot_run_pre_flight] Filtered to {len(active_king_alerts)} alerts active during {window_label}.")
    else:
        active_king_alerts = all_king_alerts

    # 5. Identify Bellevue & Eastside alerts for Commute Briefing
    bellevue_alerts = [alert for alert in active_king_alerts if is_bellevue_eastside_alert(alert)]
    logger.info(f"[wsdot_run_pre_flight] Identified {len(bellevue_alerts)} alerts impacting Bellevue/Eastside.")

    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (WSDOT): Found {len(active_king_alerts)} alerts in King County ({len(bellevue_alerts)} impacting Bellevue/Eastside corridors) for {window_label}.\n")

    # 6. Generate LLM Commuter Briefing for Bellevue area
    briefing = await generate_llm_commute_briefing(bellevue_alerts, timewindow_label=window_label, log_path=log_path)

    # 7. Format into structured markdown
    report_md = format_alerts_report(
        active_king_alerts,
        total_statewide=len(all_alerts),
        llm_briefing=briefing,
        timewindow_label=window_label,
        total_king_unfiltered=len(all_king_alerts)
    )

    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT SUCCESS: WSDOT report generated with {len(active_king_alerts)} King County alerts for {window_label}.\n")

    return report_md

class WsdotPlugin(BaseSitePlugin):
    description = "Washington State DOT highway traffic, road alerts, and pass reports."

    def register_actions(self, controller: Any) -> None:
        controller.action('wsdot_get_highway_alerts')(wsdot_get_highway_alerts)

    async def run_pre_flight(self, browser: BrowserSession, prompt: str, context: str, log_path: str, llm: Any) -> str:
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

# Standalone function for controller skill action
async def wsdot_get_highway_alerts(area: str = "King County", timewindow: str = "") -> str:
    """SITE-SPECIFIC SKILL: Retrieves current active highway alerts for King County from WSDOT API,
    with an AI-generated commute briefing for Bellevue and the Eastside, filtered by target time window.
    
    Args:
        area: Target county or metropolitan area (defaults to 'King County').
        timewindow: Target time window for alerts, e.g. 'current', 'this_weekend', 'following_weekend', 'tomorrow', or specific dates like 'Oct 18-20'.
    """
    all_alerts = await fetch_wsdot_highway_alerts()
    if not all_alerts:
        return "Failure: Could not retrieve alerts from WSDOT API. Check WSDOT_ACCESS_CODE."
    
    all_king_alerts = [a for a in all_alerts if is_in_king_county(a)]
    
    query_text = f"{area} {timewindow}".strip()
    window_start, window_end, window_label = await resolve_timewindow(query_text)
    
    if window_start and window_end:
        active_king_alerts = [a for a in all_king_alerts if alert_overlaps_timewindow(a, window_start, window_end)]
    else:
        active_king_alerts = all_king_alerts
        
    bellevue_alerts = [a for a in active_king_alerts if is_bellevue_eastside_alert(a)]
    briefing = await generate_llm_commute_briefing(bellevue_alerts, timewindow_label=window_label)
    return format_alerts_report(
        active_king_alerts,
        total_statewide=len(all_alerts),
        llm_briefing=briefing,
        timewindow_label=window_label,
        total_king_unfiltered=len(all_king_alerts)
    )
