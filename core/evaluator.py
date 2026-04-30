import re
import logging

FAIL_KEYWORDS = [
    "i failed", "unable to", "terminated", "no task results", "fail", "hallucination", 
    "captcha", "bot detection", "access denied", "security check", "verify you are human", "blocked"
]

DATA_HEAVY_TERMS = {'price', 'cost', 'value', 'index', 'number', 'how much', 'many', 'vix'}

STOP_WORDS = {'look', 'search', 'find', 'navigate', 'click', 'check', 'website', 'page', 'following', 'today', 'items', 'for', 'the', 'and', 'with', 'from', 'that', 'this', 'these', 'those', 'list', 'show', 'give', 'tell'}

def evaluate_result(prompt: str, final_res: str, history, log_path: str = None) -> bool:
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

    # 3. Check for failure keywords
    if any(kw in lower_res for kw in FAIL_KEYWORDS) or len(lower_res) < 20:
        is_success = False
        _log(log_path, "Validation Error: Result contains failure keywords or is too short.")

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
