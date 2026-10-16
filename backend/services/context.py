# context_manager.py
import os
import json
import httpx
from backend import config
from backend.database.models import Context

SITE_ALIASES = {
    "wsdot": ["wsdot", "highway alert", "highway alerts", "traffic alert", "traffic alerts", "wa traffic", "washington traffic", "wsdot.wa.gov", "king county alerts", "king county traffic"],
    "premarket": ["premarket", "pre-market", "pre market", "market news", "market related news", "morning market", "stock news", "financial news", "overnight news", "finnhub"],
    "safeway": ["safeway"],
    "weee": ["weee", "sayweee"],
    "sofi": ["sofi"]
}

def _detect_site_from_prompt(prompt: str) -> str:
    """Deterministic check: if prompt contains a known site skill name or alias, return it immediately.
    This prevents the LLM from hallucinating the wrong plugin when the site name or intent is explicit."""
    prompt_lower = prompt.lower()
    
    # 1. Check aliases first
    for site, aliases in SITE_ALIASES.items():
        if any(alias in prompt_lower for alias in aliases):
            return site

    # 2. Check site_skills filenames
    skill_dir = "site_skills"
    if os.path.isdir(skill_dir):
        for fname in os.listdir(skill_dir):
            if fname.endswith(".py"):
                skill_name = fname[:-3]  # strip .py
                if skill_name in prompt_lower:
                    return skill_name
    return ""

def format_bound_context_str(bound_contexts: list, prompt: str, log_path: str = "") -> tuple[str, str]:
    """Builds context string and resolves site plugin deterministically from explicitly bound task contexts.
    Completely bypasses LLM inference for 0 latency and 100% deterministic binding."""
    if not bound_contexts:
        return "", ""

    full_context = "PRIOR KNOWLEDGE / CUSTOM DATA:\n"
    combined_context_text = prompt + " "
    
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"Context Evaluator: Using {len(bound_contexts)} explicitly bound context(s):\n")
            for c in bound_contexts:
                f.write(f" - Bound Context: {c.name}\n")

    for c in bound_contexts:
        full_context += f"--- {c.name} ---\n{c.content}\n\n"
        combined_context_text += f"{c.name} {c.content} "

    # Deterministic site detection: prompt first, then bound context content
    site_plugin = _detect_site_from_prompt(prompt)
    if not site_plugin:
        site_plugin = _detect_site_from_prompt(combined_context_text)

    return full_context + "USE THIS TO INFORM YOUR ACTIONS.\n\n", site_plugin

async def get_relevant_context_str(db, prompt: str, log_path: str) -> tuple[str, str]:
    """Uses LLM to identify relevant context and the target site plugin in one step."""
    contexts = db.query(Context).all()
    if not contexts:
        return "", ""

    # Deterministic guard: if the prompt explicitly names a known skill, lock it in
    # immediately — prevents LLM from hallucinating the wrong site plugin.
    forced_site = _detect_site_from_prompt(prompt)

    eval_prompt = (
        f"USER TASK: {prompt}\n\n"
        "DATABASE CONTEXT ENTRIES:\n"
    )
    for i, c in enumerate(contexts):
        eval_prompt += f"[{i}] {c.name}: {c.content[:250]}\n---\n"
    
    eval_prompt += (
        "\nINSTRUCTIONS:\n"
        "1. Select context entries that describe how to use the WEBSITES or GROUPS mentioned in the task (e.g., Safeway).\n"
        "2. Even if the specific item (like 'steak') isn't mentioned, if the context explains the 'Deals' or 'Coupons' process for that site, it IS relevant.\n"
        "3. Identify the core domain name (e.g., 'safeway').\n"
        "JSON ONLY: {\"relevant_indices\": [int, ...], \"site_plugin\": \"string or NONE\"}"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": eval_prompt}],
                    "stream": False,
                    "temperature": 0
                },
                timeout=config.LLM_TIMEOUT
            )
            res_json = resp.json()
            data_raw = "{}"
            if "choices" in res_json and len(res_json["choices"]) > 0:
                data_raw = res_json["choices"][0].get("message", {}).get("content", "{}")
            elif "message" in res_json:
                data_raw = res_json.get("message", {}).get("content", "{}")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Context Evaluator: Raw LLM response: {data_raw}\n")
            from backend.services.llm import clean_json_text
            data = json.loads(clean_json_text(data_raw))
            indices = data.get("relevant_indices", [])
            site_plugin = data.get("site_plugin", "NONE").lower()
            if site_plugin == "none": site_plugin = ""

            # Deterministic result always wins over LLM guess
            if forced_site:
                if site_plugin and site_plugin != forced_site:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"Context Evaluator: Overriding LLM site_plugin '{site_plugin}' → '{forced_site}' (deterministic match)\n")
                site_plugin = forced_site

            with open(log_path, "a", encoding="utf-8") as f:
                if not indices:
                    f.write("Context Evaluator: No relevant context found.\n")
                    return "", site_plugin
                
                f.write(f"Context Evaluator: Selected {len(indices)} entries. Site Plugin: {site_plugin}\n")
                full_context = "PRIOR KNOWLEDGE:\n"
                for i in indices:
                    if 0 <= int(i) < len(contexts):
                        c = contexts[int(i)]
                        f.write(f" - Using Context: {c.name}\n")
                        full_context += f"--- {c.name} ---\n{c.content}\n\n"
            return full_context + "USE THIS TO INFORM YOUR ACTIONS.\n\n", site_plugin
    except Exception as e:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"Context analysis failed: {e}\n")
        # Fall back to deterministic detection if LLM fails entirely
        return "", forced_site
