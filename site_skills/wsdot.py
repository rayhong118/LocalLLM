# site_skills/wsdot.py
import logging
import os
import re
import json
import asyncio
from datetime import datetime
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

def format_alerts_report(king_alerts: List[Dict[str, Any]], total_statewide: int) -> str:
    """Formats King County highway alerts into a structured, readable Markdown report."""
    if not king_alerts:
        return (
            "# 🚗 WSDOT Highway Alerts: King County Area\n\n"
            "✅ **No active highway alerts or major traffic disruptions currently reported in King County.**\n\n"
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
    md.append(f"**Found {len(king_alerts)} active alerts** affecting highways in King County ({total_statewide} alerts statewide).\n")

    # Critical Alerts Callout
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

    # Corridor Breakdown
    md.append("### 🛣️ Alerts by Highway Corridor\n")

    # Sort corridors: major interstates first (I-405, I-5, I-90), then SRs
    def corridor_sort_key(c_name: str):
        order = {"I-5": 1, "I-405": 2, "I-90": 3, "SR 520": 4, "SR 18": 5, "SR 167": 6, "SR 99": 7}
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
    filters for King County area alerts, and produces a structured report.
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

    # 2. Filter for alerts located in King County
    king_alerts = [alert for alert in all_alerts if is_in_king_county(alert)]
    logger.info(f"[wsdot_run_pre_flight] Identified {len(king_alerts)} King County alerts out of {len(all_alerts)} total.")

    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT (WSDOT): Found {len(king_alerts)} alerts in King County (out of {len(all_alerts)} statewide).\n")

    # 3. Format into structured markdown
    report_md = format_alerts_report(king_alerts, total_statewide=len(all_alerts))

    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT SUCCESS: WSDOT report generated with {len(king_alerts)} King County alerts.\n")

    return report_md

class WsdotPlugin(BaseSitePlugin):
    """Plugin wrapper for dynamic loading by PluginRegistry."""
    async def run_pre_flight(self, browser: BrowserSession, prompt: str, context: str, log_path: str, llm: Any) -> str:
        return await wsdot_run_pre_flight(browser, prompt, context, log_path, llm)

# Standalone function for controller skill action
async def wsdot_get_highway_alerts(area: str = "King County") -> str:
    """SITE-SPECIFIC SKILL: Retrieves current active highway alerts for King County from WSDOT API.
    
    Args:
        area: Target county or metropolitan area (defaults to 'King County').
    """
    all_alerts = await fetch_wsdot_highway_alerts()
    if not all_alerts:
        return "Failure: Could not retrieve alerts from WSDOT API. Check WSDOT_ACCESS_CODE."
    
    king_alerts = [a for a in all_alerts if is_in_king_county(a)]
    return format_alerts_report(king_alerts, total_statewide=len(all_alerts))
