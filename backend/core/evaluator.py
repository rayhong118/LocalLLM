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
    Generic rule-based fallback for structured summaries when no site-specific plugin evaluator matched.
    Returns 'SUCCESS', 'PARTIAL_SUCCESS', 'NO_RESULT', or None if not deterministically matchable.
    """
    # Explicit "0 results found" or "no matching items" across whole run
    if re.search(r'\b(?:0\s+(?:results|items|deals|records|matches)\s+found|no\s+matching\s+(?:results|deals|items|records))\b', lower_res):
        return "NO_RESULT"

    return None

async def evaluate_result(prompt: str, final_res: str, history=None, log_path: str = None, site_key: str = "") -> str:
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

    # 3. Deterministic skill-specific evaluator (if a site plugin is active)
    if site_key:
        try:
            from backend.core.plugin import PluginRegistry
            plugin = PluginRegistry.get_plugin(site_key)
            if plugin:
                skill_outcome = plugin.evaluate_outcome(final_res)
                if skill_outcome in ("SUCCESS", "PARTIAL_SUCCESS", "NO_RESULT", "FAILED"):
                    _log(log_path, f"Outcome Evaluation (Skill '{site_key}'): Outcome={skill_outcome}")
                    return skill_outcome
        except Exception as e:
            _log(log_path, f"Outcome Evaluation: Skill evaluator error: {e}")

    # 4. Generic deterministic structured check (fast and accurate for generic summaries)
    deterministic_outcome = _evaluate_structured_summary(lower_res)
    if deterministic_outcome:
        _log(log_path, f"Outcome Evaluation (Generic Deterministic): Outcome={deterministic_outcome}")
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
