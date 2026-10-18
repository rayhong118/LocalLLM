# context_manager.py
import os
import json
import httpx
from backend import config
from backend.database.models import Context

def _detect_site_from_prompt(prompt: str) -> str:
    """Checks if any installed site skill name appears directly in the prompt text (case-insensitive).
    Provides a 0ms fast-path when the target site/skill is explicitly named."""
    prompt_lower = prompt.lower()
    skill_dir = "site_skills"
    if os.path.isdir(skill_dir):
        for fname in os.listdir(skill_dir):
            if fname.endswith(".py") and not fname.startswith("__"):
                skill_name = fname[:-3]  # strip .py
                if skill_name.lower() in prompt_lower:
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

SITE_SKILL_DESCRIPTIONS = {
    "safeway": "Safeway grocery deals, coupons, and product discounts.",
    "weee": "SayWeee Asian and Hispanic grocery shopping and favorites.",
    "wsdot": "Washington State DOT highway traffic, road alerts, and pass reports.",
    "premarket": "Overnight & morning pre-market financial news and macro wire.",
    "afterMarket": "After-market daily stock report, post-market prices (yfinance), and earnings catalysts.",
    "sofi": "SoFi accounts, banking, and financial services automation."
}

def get_available_site_skills() -> list[str]:
    """Dynamically discover all available site skill names from site_skills/."""
    skills = []
    if os.path.isdir("site_skills"):
        for fname in os.listdir("site_skills"):
            if fname.endswith(".py") and not fname.startswith("__"):
                skills.append(fname[:-3])
    return sorted(skills)

async def get_relevant_context_str(db, prompt: str, log_path: str) -> tuple[str, str]:
    """Uses LLM to identify relevant context and match against available site skills."""
    contexts = db.query(Context).all()
    if not contexts:
        return "", ""

    # Deterministic guard: if the prompt explicitly names a known skill, lock it in
    # immediately — prevents LLM from hallucinating the wrong site plugin.
    forced_site = _detect_site_from_prompt(prompt)
    available_skills = get_available_site_skills()
    skills_list_str = "\n".join(
        f"- {s}: {SITE_SKILL_DESCRIPTIONS.get(s, f'{s} website automation skill.')}"
        for s in available_skills
    )

    eval_prompt = (
        f"USER TASK: {prompt}\n\n"
        "DATABASE CONTEXT ENTRIES:\n"
    )
    for i, c in enumerate(contexts):
        eval_prompt += f"[{i}] {c.name}: {c.content[:250]}\n---\n"
    
    eval_prompt += (
        "\nAVAILABLE SITE SKILLS:\n"
        f"{skills_list_str}\n\n"
        "INSTRUCTIONS:\n"
        "1. Select context entries that are relevant to the user task (e.g. watchlists, account info, or workflows for the site).\n"
        "2. Match the user task to EXACTLY ONE skill from AVAILABLE SITE SKILLS (use the exact skill name), or 'NONE' if no specialized skill applies.\n"
        "JSON ONLY: {\"relevant_indices\": [int, ...], \"site_plugin\": \"skill_name or NONE\"}"
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
            site_plugin_raw = str(data.get("site_plugin", "NONE")).strip()

            # Match against available_skills case-insensitively to get the exact canonical name
            matched_skill = ""
            for s in available_skills:
                if s.lower() == site_plugin_raw.lower():
                    matched_skill = s
                    break
            site_plugin = matched_skill

            # Deterministic result always wins over LLM guess
            if forced_site:
                if site_plugin and site_plugin != forced_site:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"Context Evaluator: Overriding LLM site_plugin '{site_plugin}' → '{forced_site}' (deterministic match)\n")
                site_plugin = forced_site

            # Deterministic fallback: If LLM returned no indices, check context names against site_plugin or prompt
            if not indices and (site_plugin or forced_site):
                target = (site_plugin or forced_site).lower()
                for idx, c in enumerate(contexts):
                    c_name_lower = c.name.lower()
                    if target in ["aftermarket", "premarket"] and any(kw in c_name_lower for kw in ["stock", "watchlist", "ticker", "portfolio", "equity", "equities"]):
                        indices.append(idx)
                    elif target in c_name_lower:
                        indices.append(idx)

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
        full_context = ""
        if forced_site:
            target = forced_site.lower()
            for c in contexts:
                c_name_lower = c.name.lower()
                matched = False
                if target in ["aftermarket", "premarket"] and any(kw in c_name_lower for kw in ["stock", "watchlist", "ticker", "portfolio", "equity", "equities"]):
                    matched = True
                elif target in c_name_lower:
                    matched = True
                if matched:
                    full_context += f"--- {c.name} ---\n{c.content}\n\n"
        if full_context:
            return full_context + "USE THIS TO INFORM YOUR ACTIONS.\n\n", forced_site
        return "", forced_site
