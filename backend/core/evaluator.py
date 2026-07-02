import re
import logging
import json
import httpx
from backend import config

DATA_HEAVY_TERMS = {'price', 'cost', 'value', 'index', 'number', 'how much', 'many', 'vix'}

STOP_WORDS = {'look', 'search', 'find', 'navigate', 'click', 'check', 'website', 'page', 'following', 'today', 'items', 'for', 'the', 'and', 'with', 'from', 'that', 'this', 'these', 'those', 'list', 'show', 'give', 'tell'}

JUDGE_SYSTEM_PROMPT = (
    "You are a strict task evaluation assistant. Your job is to determine whether an AI agent "
    "successfully completed a given task based on the user's prompt and the agent's final output.\n\n"
    "Analyze the final output carefully:\n"
    "- A task is successful (true) if the agent accomplished the core objectives of the prompt "
    "or successfully reported correct, verified information (even if negative/empty, like 'no coupons found' "
    "if that is the true state of the site).\n"
    "- A task is a failure (false) if there was a critical automation/scraping error, if the agent was blocked "
    "by bot detection, or if it failed to retrieve the requested information due to system/runner errors.\n\n"
    "Output ONLY a valid JSON object with the keys 'success' (boolean) and 'reason' (string explaining your decision)."
)

def _has_failure_keywords(lower_res: str) -> bool:
    # 1. Simple checks for explicit failure/blocker phrases
    simple_fail_keywords = [
        "i failed", "terminated", "no task results", "hallucination", 
        "captcha", "bot detection", "access denied", "security check", 
        "verify you are human", "blocked"
    ]
    if any(kw in lower_res for kw in simple_fail_keywords):
        return True

    # 2. Check for "unable to"
    if "unable to" in lower_res:
        return True

    # 3. Check for fail-family words with negation and zero-count checks
    for match in re.finditer(r"\b(fail|failed|failure|failures|failing)\b", lower_res):
        start, end = match.span()
        
        preceding = lower_res[max(0, start - 25):start].strip()
        following = lower_res[end:min(len(lower_res), end + 25)].strip()

        preceding_words = re.findall(r"\b\w+\b", preceding)
        if preceding_words:
            negation_words = {"0", "zero", "no", "none", "without"}
            if any(w in negation_words for w in preceding_words[-3:]):
                continue

        if re.match(r"^(?:\s*\w+)*\s*:\s*(?:(?:0|zero|none|null|false)\b|\[\s*\])", following):
            continue

        return True

    return False

async def evaluate_result(prompt: str, final_res: str, history, log_path: str = None) -> bool:
    """
    Evaluates the final result of an agent run to determine if it was successful.
    Returns True if successful, False otherwise.
    """
    if not final_res or final_res == "No result extracted":
        return False

    lower_res = final_res.lower()
    is_success = True
    
    # 1. Check for explicit history success
    if history and history.is_successful() is False:
        is_success = False

    # 2. Filter out transient LLM errors
    if history and history.has_errors():
        excluded_phrases = ["closed pipe", "resourcewarning", "connection closed", "failed to parse", "invalid model output format", "timed out"]
        critical_errors = [e for e in history.errors() if not any(x in str(e).lower() for x in excluded_phrases)]
        if critical_errors:
            is_success = False

    # 3. LLM Judge Evaluation (with context-aware keyword fallback)
    if is_success:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": config.LLM_MODEL,
                        "messages": [
                            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                            {"role": "user", "content": f"User Prompt: {prompt}\n\nAgent Final Output:\n{final_res}"}
                        ],
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0, "num_ctx": 8192}
                    },
                    timeout=15.0
                )
                if resp.status_code == 200:
                    res_data = resp.json()
                    content = res_data.get("message", {}).get("content", "")
                    judge_result = json.loads(content)
                    is_success = judge_result.get("success", True)
                    reason = judge_result.get("reason", "")
                    _log(log_path, f"LLM Judge Decision: success={is_success}, reason='{reason}'")
                else:
                    _log(log_path, f"LLM Judge API returned status {resp.status_code}. Falling back to keywords.")
                    if _has_failure_keywords(lower_res) or len(lower_res) < 20:
                        is_success = False
        except Exception as e:
            _log(log_path, f"LLM Judge evaluation failed: {e}. Falling back to keywords.")
            if _has_failure_keywords(lower_res) or len(lower_res) < 20:
                is_success = False

    if not is_success:
        _log(log_path, "Validation Error: Result failed success evaluation criteria.")

    # 4. Keyword verification
    en_keywords = [w for w in re.findall(r'[a-z]{3,}', prompt.lower()) if w not in STOP_WORDS]
    cn_keywords = re.findall(r'[\u4e00-\u9fff]{2,}', prompt)
    core_keywords = sorted(list(set(en_keywords + cn_keywords)), key=len, reverse=True)

    if core_keywords:
        # Check if result mentions core keywords
        if not any(kw in lower_res for kw in core_keywords):
            _log(log_path, f"Validation Error: Result does not mention core keywords {core_keywords}")
            is_success = False
        
        # Check for numerical data in data-heavy prompts
        if any(term in prompt.lower() for term in DATA_HEAVY_TERMS) and not re.search(r'\d+', lower_res):
            is_success = False
            _log(log_path, "Validation Error: Data-heavy goal but no numerical values found in result.")

    return is_success

def _log(log_path: str, message: str):
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{message}\n")
    else:
        logging.info(message)
