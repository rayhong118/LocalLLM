import re
import logging
import json
import httpx
from backend import config

logger = logging.getLogger(__name__)

DATA_HEAVY_TERMS = {'price', 'cost', 'value', 'index', 'number', 'how much', 'many', 'vix'}

STOP_WORDS = {
    'look', 'search', 'find', 'navigate', 'click', 'check', 'website', 'page', 
    'following', 'today', 'items', 'for', 'the', 'and', 'with', 'from', 'that', 
    'this', 'these', 'those', 'list', 'show', 'give', 'tell', 'coupons', 'coupon', 'deals', 'deal'
}

JUDGE_SYSTEM_PROMPT = (
    "You are an objective task evaluation assistant. Your job is to classify the final outcome of an AI agent task "
    "based on the user's prompt and the agent's final output.\n\n"
    "Classify the outcome into EXACTLY ONE of the following 4 tiers:\n"
    "- 'SUCCESS': The workflow ran to completion and fully satisfied all requested items/goals "
    "(e.g., all coupons found/clipped, all data extracted, all items added).\n"
    "- 'PARTIAL_SUCCESS': The workflow ran to completion, but only a subset of requested items or information "
    "was available on the target website (e.g. 5 of 7 items were found/clipped; some items out of stock or had no deals).\n"
    "- 'NO_RESULT': The workflow ran to completion and checked valid sources/categories, but zero matching results, "
    "deals, or records exist on the website for the query (clean execution, but empty real-world data).\n"
    "- 'FAILED': The task suffered an unhandled technical error, automation crash, bot detection/CAPTCHA block, "
    "or terminated abnormally before finishing the workflow.\n\n"
    "Output ONLY a valid JSON object with the keys 'status' (one of 'SUCCESS', 'PARTIAL_SUCCESS', 'NO_RESULT', 'FAILED') "
    "and 'reason' (short string explaining your decision)."
)

def _has_technical_blockers(lower_res: str) -> bool:
    """Checks for explicit bot detection, captcha, or catastrophic runner aborts."""
    fatal_keywords = [
        "captcha", "bot detection", "access denied", "security check", 
        "verify you are human", "fatal agent error", "preflight_fatal",
        "uncaught exception", "browser crashed", "net::err_"
    ]
    return any(kw in lower_res for kw in fatal_keywords)

def _evaluate_structured_summary(lower_res: str) -> str | None:
    """
    Deterministic rule-based evaluator for structured skill summaries (e.g. Safeway, Weee, scrapers).
    Returns 'SUCCESS', 'PARTIAL_SUCCESS', 'NO_RESULT', or None if not deterministically matchable.
    """
    # 1. Safeway / Coupon style summary: "X items searched | Y clipped | Z not found"
    summary_match = re.search(
        r'(\d+)\s*(?:items?|products?)\s*searched[^\n\r]*?(\d+)\s*clipped[^\n\r]*?(?:(\d+)\s*auto-sale[^\n\r]*?)?(\d+)\s*not\s*found',
        lower_res
    )
    if summary_match:
        searched = int(summary_match.group(1))
        clipped = int(summary_match.group(2))
        auto_sale = int(summary_match.group(3)) if summary_match.group(3) else 0
        not_found = int(summary_match.group(4))
        
        positives = clipped + auto_sale
        if positives > 0 and not_found > 0:
            return "PARTIAL_SUCCESS"
        elif positives > 0 and not_found == 0:
            return "SUCCESS"
        elif positives == 0 and not_found > 0:
            return "NO_RESULT"

    # 2. Section check: Has explicit "No Coupons Found" or "Not Found" section
    has_not_found_section = bool(re.search(r'(?:###|\*\*)\s*(?:❌\s*)?no\s+(?:coupons|items|deals|products)\s+found', lower_res))
    has_clipped_section = bool(re.search(r'(?:✅\s*clipped|➖\s*auto-sale|successfully clipped|added to cart)', lower_res))
    
    if has_not_found_section and has_clipped_section:
        return "PARTIAL_SUCCESS"
    if has_not_found_section and not has_clipped_section:
        # Check if anything positive was actually clipped or retrieved
        if not re.search(r'\b(clipped \d+|found \d+ deals|\$\d+\.\d+)\b', lower_res):
            return "NO_RESULT"

    # 3. Explicit "0 results found" or "no matching deals" across whole run
    if re.search(r'\b(?:0 deals|0 coupons|0 items|no matching (?:deals|coupons|items|results))\b', lower_res):
        if not has_clipped_section and not re.search(r'clipped \d+', lower_res):
            return "NO_RESULT"

    # 4. WSDOT Traffic / Alerts style summary: "X alerts found in King County ... out of N statewide alerts"
    wsdot_match = re.search(r'(\d+)\s*alerts?\s*(?:found\s*in|in)\s*([a-z\s]+?)\s*.*?out\s*of\s*(\d+)\s*statewide', lower_res)
    if wsdot_match:
        alerts_found = int(wsdot_match.group(1))
        return "SUCCESS" if alerts_found > 0 else "NO_RESULT"

    # 5. Market Report style summary (Pre-Market / After-Market)
    market_rep_match = re.search(r'daily market report generated[^\n\r]*?(\d+)\s*(?:major\s+)?indices\s+tracked', lower_res)
    if market_rep_match:
        indices_count = int(market_rep_match.group(1))
        return "SUCCESS" if indices_count > 0 else "NO_RESULT"

    news_match = re.search(r'(\d+)\s*(?:market\s+|macro\s+)?news\s+items?\s*(?:collected|found|retrieved)', lower_res)
    if news_match:
        news_found = int(news_match.group(1))
        return "SUCCESS" if news_found > 0 else "NO_RESULT"

    return None

async def evaluate_result(prompt: str, final_res: str, history=None, log_path: str = None) -> str:
    """
    Evaluates the final result of an agent run into a generic 4-tier outcome:
    - 'SUCCESS'
    - 'PARTIAL_SUCCESS'
    - 'NO_RESULT'
    - 'FAILED'
    """
    if not final_res or final_res in ("No result extracted", "PREFLIGHT_FATAL", ""):
        _log(log_path, "Outcome Evaluation: Missing or empty final result -> FAILED")
        return "FAILED"

    lower_res = final_res.lower()

    # 1. Technical blocker / crash check
    if _has_technical_blockers(lower_res):
        _log(log_path, "Outcome Evaluation: Detected technical blocker / bot check -> FAILED")
        return "FAILED"

    # 2. Check for critical history errors (excluding transient/ignorable warnings)
    if history and history.has_errors():
        excluded_phrases = ["closed pipe", "resourcewarning", "connection closed", "failed to parse", "invalid model output format", "timed out"]
        critical_errors = [e for e in history.errors() if not any(x in str(e).lower() for x in excluded_phrases)]
        if critical_errors:
            _log(log_path, f"Outcome Evaluation: Critical browser history errors {critical_errors} -> FAILED")
            return "FAILED"

    # 3. Deterministic structured check (fast and 100% accurate for structured reports)
    deterministic_outcome = _evaluate_structured_summary(lower_res)
    if deterministic_outcome:
        _log(log_path, f"Outcome Evaluation (Deterministic): Outcome={deterministic_outcome}")
        return deterministic_outcome

    # 4. LLM Judge Evaluation (Generic for arbitrary prompts & open-ended tasks)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": f"User Prompt: {prompt}\n\nAgent Final Output:\n{final_res}"}
                    ],
                    "stream": False,
                    "temperature": 0
                },
                timeout=60
            )
            if resp.status_code == 200:
                res_data = resp.json()
                content = ""
                if "choices" in res_data and len(res_data["choices"]) > 0:
                    content = res_data["choices"][0].get("message", {}).get("content", "")
                elif "message" in res_data:
                    content = res_data.get("message", {}).get("content", "")

                from backend.services.llm import clean_json_text
                judge_result = json.loads(clean_json_text(content))
                status = str(judge_result.get("status", "")).strip().upper()
                reason = judge_result.get("reason", "")

                if status in ("SUCCESS", "PARTIAL_SUCCESS", "NO_RESULT", "FAILED"):
                    _log(log_path, f"LLM Judge Decision: status={status}, reason='{reason}'")
                    return status
            else:
                _log(log_path, f"LLM Judge API returned status {resp.status_code}. Using heuristic fallback.")
    except Exception as e:
        _log(log_path, f"LLM Judge evaluation failed: {e}. Using heuristic fallback.")

    # 5. Heuristic Fallback
    # Check if there were negative mentions
    has_negatives = any(k in lower_res for k in ["no deals", "not found", "no coupon", "no match", "out of stock", "0 items"])
    has_positives = any(k in lower_res for k in ["clipped", "coupon(s)", "added", "deal price", "$", "found"])
    
    # Check core keyword presence
    en_keywords = [w for w in re.findall(r'[a-z]{3,}', prompt.lower()) if w not in STOP_WORDS]
    cn_keywords = re.findall(r'[\u4e00-\u9fff]{2,}', prompt)
    core_keywords = sorted(list(set(en_keywords + cn_keywords)), key=len, reverse=True)
    keyword_coverage = sum(1 for kw in core_keywords if kw in lower_res) if core_keywords else 0

    if has_positives and (has_negatives or (core_keywords and keyword_coverage < len(core_keywords))):
        outcome = "PARTIAL_SUCCESS"
    elif has_positives:
        outcome = "SUCCESS"
    elif has_negatives:
        outcome = "NO_RESULT"
    else:
        outcome = "SUCCESS" if len(lower_res) > 40 else "NO_RESULT"

    _log(log_path, f"Outcome Evaluation (Heuristic): Outcome={outcome}")
    return outcome

def _log(log_path: str, message: str):
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{message}\n")
    else:
        logger.info(message)
