# context_manager.py
import json
import httpx
import config
from database import Context

async def get_relevant_context_str(db, prompt: str, log_path: str) -> tuple[str, str]:
    """Uses LLM to identify relevant context and the target site plugin in one step."""
    contexts = db.query(Context).all()
    if not contexts:
        return "", ""

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
                "http://localhost:11434/api/chat",
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": eval_prompt}],
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": {"temperature": 0, "num_ctx": 4096}
                },
                timeout=config.LLM_TIMEOUT
            )
            data_raw = resp.json().get("message", {}).get("content", "{}")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Context Evaluator: Raw LLM response: {data_raw}\n")
            data = json.loads(data_raw)
            indices = data.get("relevant_indices", [])
            site_plugin = data.get("site_plugin", "NONE").lower()
            if site_plugin == "none": site_plugin = ""

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
        return "", ""
