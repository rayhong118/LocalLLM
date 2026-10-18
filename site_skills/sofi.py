# site_skills/sofi.py
import asyncio
import html as html_mod
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend import config
from backend.core.plugin import BaseSitePlugin
from backend.services.llm import clean_json_text
from browser_use import BrowserSession

logger = logging.getLogger(__name__)

# ==========================================
# 1. Precompiled Regular Expressions
# ==========================================

RE_LEADERSHIP = re.compile(r'\b(manager|director|vp|vice\s+president|head|chief)\b', re.IGNORECASE)
RE_EXPLICIT_FRONTEND = re.compile(r'\b(frontend|front\s+end|fullstack|full\s+stack|web|react|javascript|typescript|angular|vue|ui|ux)\b', re.IGNORECASE)
RE_UNLIKELY = re.compile(r'\b(backend|data\s+engineer|data\s+infrastructure|devops|infrastructure|crypto|database|security\s+engineer|firmware|site\s+reliability|sre)\b', re.IGNORECASE)
RE_GENERIC_ENG = re.compile(r'\b(software\s+engineer|software\s+developer|developer|programmer|systems\s+engineer|application\s+engineer|engineer)\b', re.IGNORECASE)
RE_NON_ENG = re.compile(r'\b(data\s+science|data\s+scientist|analyst|teller|agent|underwriter|specialist|recruiter|marketing|sales|coach|designer|product\s+manager|project\s+manager|program\s+manager|scrum\s+master|incident\s+commander|business\s+manager|controls|payroll|security\s+manager|photographer|hr|operations|treasury|rewards|billing|compliance|fraud)\b', re.IGNORECASE)

RE_TAGS = re.compile(r'<[^>]+>')
RE_REACT = re.compile(r'\b(react|reactjs|react\.js)\b', re.IGNORECASE)
RE_FULLSTACK = re.compile(r'\b(fullstack|full\s*-\s*stack|full\s+stack)\b', re.IGNORECASE)
RE_FRONTEND = re.compile(r'\b(frontend|front\s*-\s*end|front\s+end)\b', re.IGNORECASE)
RE_TYPESCRIPT = re.compile(r'\b(typescript|type\s+script)\b', re.IGNORECASE)

CUTOFF_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'^Compensation\b', r'^Benefits\b', r'^Compensation and Benefits\b',
        r'^SoFi provides equal', r'^The Company hires',
        r'^Internal Employees\b', r'^Due to insurance',
        r'^Pursuant to the', r'^New York applicants',
        r'^If you are a current employee',
    ]
]


# ==========================================
# 2. Text Processing & Extraction Helpers
# ==========================================

def _get_metadata_value(job: Dict[str, Any], name: str) -> str:
    """Helper to extract custom metadata fields like Pay Range or Time Type."""
    for item in job.get("metadata", []):
        if item.get("name") == name:
            val = item.get("value")
            if not val:
                continue
            if isinstance(val, dict):
                min_v, max_v = val.get("min_value"), val.get("max_value")
                unit = val.get("unit", "USD")
                if min_v and max_v:
                    try:
                        return f"${float(min_v):,.2f} - ${float(max_v):,.2f} {unit}"
                    except ValueError:
                        return f"{min_v} - {max_v} {unit}"
            return str(val)
    return ""


def _clean_html_to_text(html_content: str) -> str:
    """Convert raw HTML into clean plain text."""
    text = re.sub(r'</?(p|div|li|br|h1|h2|h3|h4|h5|ul|ol)[^>]*>', '\n', html_content)
    text = RE_TAGS.sub(' ', text)
    text = html_mod.unescape(text)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)


def _extract_job_essentials(html_content: str) -> str:
    """Extract role duties, requirements, and qualifications, dropping boilerplate."""
    body = re.sub(r'<div\s+class="content-intro"[^>]*>.*?</div>\s*</div>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r'<div\s+class="content-conclusion"[^>]*>.*', '', body, flags=re.DOTALL | re.IGNORECASE)
    text = _clean_html_to_text(body)

    lines = text.split('\n')
    cut_idx = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(pat.match(stripped) for pat in CUTOFF_PATTERNS):
            cut_idx = i
            break

    res = '\n'.join(lines[:cut_idx]).strip()
    return res if res else "(No role-specific content extracted)"


# ==========================================
# 3. Filtering & Classification Logic
# ==========================================

def classify_job_title(title: str) -> str:
    """
    Categorizes job title in a single pass:
    - 'EXPLICIT': Direct Frontend / Fullstack titles.
    - 'CANDIDATE': Generic Software Engineering titles needing description check.
    - 'SKIP': Leadership, non-engineering, or unlikely backend/infra titles.
    """
    norm = re.sub(r'[-_]', ' ', title.lower())
    if RE_LEADERSHIP.search(norm):
        return "SKIP"
    if RE_EXPLICIT_FRONTEND.search(norm):
        return "EXPLICIT"
    if RE_UNLIKELY.search(norm):
        return "SKIP"
    if RE_GENERIC_ENG.search(norm) and not RE_NON_ENG.search(norm):
        return "CANDIDATE"
    return "SKIP"


def check_description_heuristics(clean_text: str) -> Tuple[bool, str]:
    """Fast-path check for fullstack/frontend keywords in job description."""
    match = RE_FULLSTACK.search(clean_text) or RE_FRONTEND.search(clean_text)
    if match:
        return True, f"Found '{match.group(0)}' in description (heuristic match)"
    return False, ""


async def _check_job_description_with_llm(
    client: httpx.AsyncClient, title: str, clean_desc: str, log_path: str = ""
) -> Tuple[bool, str]:
    """Uses LLM to verify if a generic software engineering role has a frontend/fullstack focus."""
    desc_sample = clean_desc[:3500]

    system_prompt = (
        "You are a job description analyzer. Evaluate if the given job description is for a software engineer role "
        "that is either Full Stack (with client-side focus) or has significant frontend developer responsibilities "
        "(e.g., UI, React, web client, frontend architecture, browser environments).\n\n"
        "Respond with ONLY a valid JSON object in this format:\n"
        "{\"is_match\": true, \"reason\": \"Brief explanation of frontend focus\"}\n"
        "or\n"
        "{\"is_match\": false, \"reason\": \"No significant frontend/client-side focus\"}\n"
        "Do NOT include any other text."
    )

    user_msg = f"Job Title: {title}\n\nDescription:\n{desc_sample}"

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
                "temperature": 0
            },
            timeout=config.LLM_TIMEOUT
        )
        if resp.status_code == 200:
            res_json = resp.json()
            raw = (
                res_json.get("choices", [{}])[0].get("message", {}).get("content")
                or res_json.get("message", {}).get("content", "{}")
            )
            data = json.loads(clean_json_text(raw))
            return data.get("is_match", False), data.get("reason", "")
    except Exception as e:
        logger.error(f"[sofi] Error checking description for {title}: {e}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"LLM check error for {title}: {e}\n")
    return False, ""


def _format_job_item(job: Dict[str, Any]) -> List[str]:
    """Render a single job posting markdown card."""
    lines = [f"- **[{job['title']}]({job['url']})**"]
    if job.get("llm_reason"):
        lines.append(f"  - **💡 Reason:** *{job['llm_reason']}*")
    lines.append(f"  - **📍 Location:** {job['location']}")
    if job.get("pay_range"):
        lines.append(f"  - **💵 Compensation:** {job['pay_range']}")
    lines.append(
        f"  - **🛠️ Tech Stack:** React: **{'Yes' if job['react_present'] else 'No'}** | "
        f"Fullstack: **{'Yes' if job['fullstack_present'] else 'No'}** | "
        f"TypeScript: **{'Yes' if job['ts_present'] else 'No'}**"
    )
    if job.get("first_published"):
        lines.append(f"  - **📅 Published:** {job['first_published'][:10]}")
    lines.append("")
    return lines


# ==========================================
# 4. Main Pre-Flight Orchestrator
# ==========================================

async def sofi_run_pre_flight(
    browser: Optional[BrowserSession] = None,
    prompt: str = "",
    context_str: str = "",
    log_path: str = "",
    llm: Any = None
) -> str:
    """Site-specific pre-flight automation for SoFi Greenhouse job board."""
    try:
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n--- PRE-FLIGHT (SoFi Jobs): Fetching openings ---\n")

        api_url = "https://boards-api.greenhouse.io/v1/boards/sofi/jobs?content=true"
        async with httpx.AsyncClient() as client:
            resp = await client.get(api_url, timeout=30)
            if resp.status_code != 200:
                err_msg = f"Failed to fetch job board API. Status: {resp.status_code}"
                logger.error(f"[sofi_run_pre_flight] {err_msg}")
                if log_path:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"PRE-FLIGHT ERROR: {err_msg}\n")
                return ""

            jobs_data = resp.json().get("jobs", [])

        if not jobs_data:
            if log_path:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("PRE-FLIGHT: No job listings returned from API.\n")
            return "No job listings found on SoFi careers page."

        explicit_matches: List[Dict[str, Any]] = []
        heuristic_matches: List[Dict[str, Any]] = []
        llm_candidates: List[Dict[str, Any]] = []
        total_count = len(jobs_data)

        for job in jobs_data:
            title = job.get("title", "")
            title_cat = classify_job_title(title)
            if title_cat == "SKIP":
                continue

            content = job.get("content", "")
            clean_text = _clean_html_to_text(content)
            clean_lower = clean_text.lower()

            react_present = bool(RE_REACT.search(clean_lower))
            fullstack_present = bool(RE_FULLSTACK.search(clean_lower))

            # Mandatory requirement: either React or Fullstack must be in the description
            if not react_present and not fullstack_present:
                continue

            ts_present = bool(RE_TYPESCRIPT.search(clean_lower))
            pay_range = _get_metadata_value(job, "Pay Range") or _get_metadata_value(job, "Pay Transparency Range")

            job_item = {
                "title": title,
                "location": job.get("location", {}).get("name", "Remote/Various"),
                "pay_range": pay_range,
                "time_type": _get_metadata_value(job, "Time Type") or "Full time",
                "url": job.get("absolute_url", ""),
                "updated_at": job.get("updated_at", ""),
                "first_published": job.get("first_published", ""),
                "clean_text": clean_text,
                "content": content,
                "react_present": react_present,
                "fullstack_present": fullstack_present,
                "ts_present": ts_present
            }

            if title_cat == "EXPLICIT":
                explicit_matches.append(job_item)
            elif title_cat == "CANDIDATE":
                has_heur, reason = check_description_heuristics(clean_lower)
                if has_heur:
                    job_item["llm_reason"] = reason
                    heuristic_matches.append(job_item)
                else:
                    llm_candidates.append(job_item)

        # Log Step 1 classification results
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- SoFi Step 1: Title & React Filter Results ---\n")
                f.write(f"Total jobs evaluated: {total_count}\n")
                f.write(f"Explicit Frontend/Fullstack matches ({len(explicit_matches)})\n")
                f.write(f"Heuristic description matches ({len(heuristic_matches)})\n")
                f.write(f"Potential developer jobs sent to LLM ({len(llm_candidates)})\n")
                f.write("-------------------------------------------------\n\n")

        # Concurrently evaluate borderline candidates with local LLM (max 3 at a time)
        matched_candidates = list(heuristic_matches)
        if llm_candidates:
            sem = asyncio.Semaphore(3)

            async def _throttled_check(client: httpx.AsyncClient, item: Dict[str, Any]) -> Tuple[bool, str]:
                async with sem:
                    return await _check_job_description_with_llm(client, item["title"], item["clean_text"], log_path)

            async with httpx.AsyncClient(trust_env=False) as client:
                tasks = [_throttled_check(client, j) for j in llm_candidates]
                llm_results = await asyncio.gather(*tasks)

            for job_item, (is_match, reason) in zip(llm_candidates, llm_results):
                if is_match:
                    job_item["llm_reason"] = reason
                    matched_candidates.append(job_item)

        # Generate structured markdown report
        summary_lines = [
            "# 💼 SoFi Jobs Monitoring Results\n",
            "Searched SoFi jobs using the **2-step Frontend/Fullstack search strategy**:\n"
        ]

        total_matched = len(explicit_matches) + len(matched_candidates)

        if not total_matched:
            summary_lines.append("❌ **No matching job openings found currently.**")
        else:
            if explicit_matches:
                summary_lines.append(f"### 🎯 Step 1: Explicit Frontend / Fullstack Titles ({len(explicit_matches)})")
                for job in explicit_matches:
                    summary_lines.extend(_format_job_item(job))

            if matched_candidates:
                summary_lines.append(f"### 🔍 Step 2: Generic Engineering Roles with Frontend Focus ({len(matched_candidates)})")
                for job in matched_candidates:
                    summary_lines.extend(_format_job_item(job))

        summary_lines.append("***")
        summary_lines.append(
            f"📊 **SUMMARY:** {total_count} total careers | "
            f"{len(explicit_matches)} explicit title matches | "
            f"{len(llm_candidates)} generic tech roles checked | "
            f"{len(matched_candidates)} matched via description"
        )

        result_md = "\n".join(summary_lines)
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT SUCCESS: Found {total_matched} matching roles total.\n")

        return result_md

    except Exception as e:
        logger.error(f"[sofi_run_pre_flight] Error: {e}")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT ERROR: {e}\n")
        return ""


# ==========================================
# 5. Plugin Definition
# ==========================================

class SofiPlugin(BaseSitePlugin):
    description = "SoFi Greenhouse careers and engineering job board monitoring."

    async def run_pre_flight(
        self, browser: Optional[BrowserSession], prompt: str, context: str, log_path: str = "", llm: Any = None
    ) -> str:
        return await sofi_run_pre_flight(browser, prompt, context, log_path, llm)

    def evaluate_outcome(self, result_text: str) -> Optional[str]:
        lower_res = result_text.lower()
        if "no matching job openings found" in lower_res:
            return "NO_RESULT"
        if "explicit title matches" in lower_res or "matched via description" in lower_res or "pre-flight success" in lower_res:
            return "SUCCESS"
        return None
