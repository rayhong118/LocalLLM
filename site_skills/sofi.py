# site_skills/sofi.py
import logging
import httpx
import json
import asyncio
import re
from typing import Any, List, Dict
from core.plugin import BaseSitePlugin
from browser_use import BrowserSession
import config

logger = logging.getLogger(__name__)

def _get_metadata_value(job: Dict[str, Any], name: str) -> str:
    """Helper to extract custom metadata fields like Pay Range or Time Type."""
    metadata = job.get("metadata", [])
    for item in metadata:
        if item.get("name") == name:
            val = item.get("value")
            if not val:
                continue
            if isinstance(val, dict):
                min_v = val.get("min_value")
                max_v = val.get("max_value")
                unit = val.get("unit", "USD")
                if min_v and max_v:
                    try:
                        return f"${float(min_v):,.2f} - ${float(max_v):,.2f} {unit}"
                    except ValueError:
                        return f"{min_v} - {max_v} {unit}"
            return str(val)
    return ""

def is_manager_role(title: str) -> bool:
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    # Use word boundaries to prevent matching "headless" for "head" or "mvp" for "vp"
    pattern = r'\b(manager|director|vp|vice\s+president|head|chief)\b'
    return bool(re.search(pattern, title_norm))

def is_explicit_frontend_fullstack(title: str) -> bool:
    if is_manager_role(title):
        return False
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    pattern = r'\b(frontend|front\s+end|fullstack|full\s+stack|web|react|javascript|typescript|angular|vue|ui|ux)\b'
    return bool(re.search(pattern, title_norm))

def is_generic_software_engineering(title: str) -> bool:
    if is_manager_role(title):
        return False
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    if is_explicit_frontend_fullstack(title):
        return False
    
    keywords = [
        "software engineer", "software developer", "developer", "programmer", "systems engineer", "application engineer"
    ]
    is_eng = any(kw in title_norm for kw in keywords) or "engineer" in title_norm
    
    # Use word boundaries to prevent substring matches (e.g. 'hr' matching 'chrome')
    exclude_pattern = r'\b(data\s+scientist|data\s+science|analyst|teller|agent|underwriter|specialist|recruiter|marketing|sales|coach|designer|product\s+manager|project\s+manager|program\s+manager|scrum\s+master|incident\s+commander|business\s+manager|controls|payroll|security\s+manager|photographer|hr|operations|treasury|rewards|billing|compliance|fraud)\b'
    
    if re.search(exclude_pattern, title_norm):
        if is_eng:
            non_eng_pattern = r'\b(product\s+manager|project\s+manager|program\s+manager|recruiter|scrum\s+master|designer|teller|underwriter|coach|photographer|analyst)\b'
            if re.search(non_eng_pattern, title_norm):
                return False
            return True
        return False
    return is_eng

def is_unlikely_frontend(title: str) -> bool:
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    unlikely = [
        "backend", "data engineer", "data infrastructure", "devops", 
        "infrastructure", "crypto", "database", "security engineer", "firmware", "site reliability"
    ]
    # Check for unlikely keywords as substrings (to catch plural forms or related words like 'cryptography')
    if any(u in title_norm for u in unlikely):
        return True
    # Check for short acronym 'sre' as a whole word to prevent false positive substring matches
    if re.search(r'\bsre\b', title_norm):
        return True
    return False

async def _check_job_description_with_llm(client: httpx.AsyncClient, title: str, description: str, log_path: str) -> tuple[bool, str]:
    """Uses LLM to verify if a generic software engineering role has a frontend/fullstack focus."""
    cleaned_desc = re.sub(r'<[^>]+>', ' ', description)  # Strip HTML tags
    cleaned_desc = re.sub(r'\s+', ' ', cleaned_desc).strip()
    cleaned_desc = cleaned_desc[:4000]  # Limit length to fit local LLM context nicely

    system_prompt = (
        "You are a job description analyzer. Evaluate if the given job description is for a software engineer role "
        "that is either Full Stack (with a decent amount of frontend/client-side focus) or has significant "
        "frontend developer responsibilities (e.g., UI, React, web client, frontend architecture, browser environments).\n\n"
        "Respond with ONLY a valid JSON object in this format:\n"
        "{\"is_match\": true, \"reason\": \"Brief explanation of frontend focus\"}\n"
        "or\n"
        "{\"is_match\": false, \"reason\": \"No significant frontend/client-side focus\"}\n"
        "Do NOT include any other text."
    )

    user_msg = f"Job Title: {title}\n\nDescription:\n{cleaned_desc}"

    try:
        resp = await client.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": config.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0, "num_ctx": 4096}
            },
            timeout=config.LLM_TIMEOUT
        )
        if resp.status_code == 200:
            data = json.loads(resp.json().get("message", {}).get("content", "{}"))
            return data.get("is_match", False), data.get("reason", "")
    except Exception as e:
        logger.error(f"[sofi] Error checking description for {title}: {e}")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"LLM check error for {title}: {e}\n")
    return False, ""

async def sofi_run_pre_flight(browser: BrowserSession, prompt: str, context_str: str, log_path: str, llm: Any) -> str:
    """Site-specific pre-flight automation for SoFi Greenhouse job board."""
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- PRE-FLIGHT (SoFi Jobs): Fetching openings ---\n")

        # Fetch Greenhouse API with content=true to get full descriptions in one go
        api_url = "https://boards-api.greenhouse.io/v1/boards/sofi/jobs?content=true"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT: Querying {api_url}...\n")

        async with httpx.AsyncClient() as client:
            resp = await client.get(api_url, timeout=30)
            if resp.status_code != 200:
                err_msg = f"Failed to fetch job board API. Status: {resp.status_code}"
                logger.error(f"[sofi_run_pre_flight] {err_msg}")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT ERROR: {err_msg}\n")
                return ""

            jobs_data = resp.json().get("jobs", [])

        if not jobs_data:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("PRE-FLIGHT: No job listings returned from API.\n")
            return "No job listings found on SoFi careers page."

        # Parse jobs and categorize
        explicit_matches = []
        llm_candidates = []
        total_count = len(jobs_data)

        for job in jobs_data:
            title = job.get("title", "")

            # Skip manager/leadership roles immediately
            if is_manager_role(title):
                continue

            location = job.get("location", {}).get("name", "Remote/Various")
            pay_range = _get_metadata_value(job, "Pay Range") or _get_metadata_value(job, "Pay Transparency Range")
            time_type = _get_metadata_value(job, "Time Type") or "Full time"
            absolute_url = job.get("absolute_url", "")
            updated_at = job.get("updated_at", "")
            first_published = job.get("first_published", "")
            content = job.get("content", "")

            # Required content checks — React is mandatory in the job description
            # Strip HTML tags first to avoid matching class names, IDs, or URLs in raw HTML content
            cleaned_content = re.sub(r'<[^>]+>', ' ', content)
            cleaned_content_lower = cleaned_content.lower()
            react_present = bool(re.search(r'\b(react|reactjs|react\.js)\b', cleaned_content_lower))
            if not react_present:
                continue

            ts_present = bool(re.search(r'\b(typescript|type\s+script)\b', cleaned_content_lower))

            job_item = {
                "title": title,
                "location": location,
                "pay_range": pay_range,
                "time_type": time_type,
                "url": absolute_url,
                "updated_at": updated_at,
                "first_published": first_published,
                "content": content,
                "react_present": react_present,
                "ts_present": ts_present
            }

            if is_explicit_frontend_fullstack(title):
                explicit_matches.append(job_item)
            elif is_generic_software_engineering(title) and not is_unlikely_frontend(title):
                llm_candidates.append(job_item)

        # Log Step 1 results (name matches and potential relevant developer jobs)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- SoFi Step 1: Title & React Filter Results ---\n")
                f.write(f"Total jobs evaluated: {total_count}\n")
                f.write(f"Explicit Frontend/Fullstack name matches ({len(explicit_matches)}):\n")
                if explicit_matches:
                    for job in explicit_matches:
                        f.write(f"  - {job['title']} | URL: {job['url']}\n")
                else:
                    f.write(f"  - None\n")
                
                f.write(f"Potential relevant developer jobs (LLM Candidates) ({len(llm_candidates)}):\n")
                if llm_candidates:
                    for job in llm_candidates:
                        f.write(f"  - {job['title']} | URL: {job['url']}\n")
                else:
                    f.write(f"  - None\n")
                f.write("-------------------------------------------------\n\n")
        except Exception as log_err:
            logger.error(f"[sofi] Error writing Step 1 logging results: {log_err}")

        # Run LLM checks on the candidates concurrently with a semaphore (max 3 at a time)
        sem = asyncio.Semaphore(3)

        async def _throttled_check(client: httpx.AsyncClient, job_item: Dict[str, Any]) -> tuple:
            async with sem:
                return await _check_job_description_with_llm(client, job_item["title"], job_item["content"], log_path)

        async with httpx.AsyncClient(trust_env=False) as client:
            tasks = [_throttled_check(client, j) for j in llm_candidates]
            llm_results = await asyncio.gather(*tasks)

        # Filter the matching candidate jobs
        matched_candidates = []
        for job_item, (is_match, reason) in zip(llm_candidates, llm_results):
            if is_match:
                job_item["llm_reason"] = reason
                matched_candidates.append(job_item)

        # Generate structured markdown report
        summary_lines = ["# 💼 SoFi Jobs Monitoring Results\n"]
        summary_lines.append("Searched SoFi jobs using the **2-step Frontend/Fullstack search strategy**:\n")

        total_matched = len(explicit_matches) + len(matched_candidates)

        if not total_matched:
            summary_lines.append("❌ **No matching job openings found currently.**")
        else:
            if explicit_matches:
                summary_lines.append(f"### 🎯 Step 1: Explicit Frontend / Fullstack Titles ({len(explicit_matches)})")
                for job in explicit_matches:
                    summary_lines.append(f"- **[{job['title']}]({job['url']})**")
                    summary_lines.append(f"  - **📍 Location:** {job['location']}")
                    summary_lines.append(f"  - **🛠️ Tech Stack:** React: **Yes** (Required) | TypeScript: **{'Yes' if job['ts_present'] else 'No'}**")
                    pub_date = job['first_published']
                    if pub_date:
                        summary_lines.append(f"  - **📅 Published:** {pub_date[:10]}")
                    summary_lines.append("")

            if matched_candidates:
                summary_lines.append(f"### 🔍 Step 2: Generic Engineering Roles with Frontend Focus ({len(matched_candidates)})")
                for job in matched_candidates:
                    summary_lines.append(f"- **[{job['title']}]({job['url']})**")
                    summary_lines.append(f"  - **💡 Reason:** *{job['llm_reason']}*")
                    summary_lines.append(f"  - **📍 Location:** {job['location']}")
                    summary_lines.append(f"  - **🛠️ Tech Stack:** React: **Yes** (Required) | TypeScript: **{'Yes' if job['ts_present'] else 'No'}**")
                    pub_date = job['first_published']
                    if pub_date:
                        summary_lines.append(f"  - **📅 Published:** {pub_date[:10]}")
                    summary_lines.append("")

        summary_lines.append("***")
        summary_lines.append(
            f"📊 **SUMMARY:** {total_count} total careers | "
            f"{len(explicit_matches)} explicit title matches | "
            f"{len(llm_candidates)} generic tech roles checked | "
            f"{len(matched_candidates)} matched via description"
        )

        result_md = "\n".join(summary_lines)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT SUCCESS: Found {total_matched} matching roles total.\n")

        return result_md

    except Exception as e:
        logger.error(f"[sofi_run_pre_flight] Error: {e}")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"PRE-FLIGHT ERROR: {e}\n")
        return ""

class SofiPlugin(BaseSitePlugin):
    async def run_pre_flight(self, browser, prompt: str, context: str, log_path: str, llm) -> str:
        return await sofi_run_pre_flight(browser, prompt, context, log_path, llm)
