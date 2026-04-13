# context_manager.py
import json
import httpx
import config
from database import Context

async def get_relevant_context_str(db, prompt: str, log_path: str) -> str:
    """Uses LLM to prune irrelevant contexts to save token window space."""
    contexts = db.query(Context).all()
    if not contexts:
        return ""

    eval_prompt = (
        f"USER TASK: {prompt}\n\n"
        "DATABASE CONTEXT ENTRIES:\n"
    )
    for i, c in enumerate(contexts):
        eval_prompt += f"[{i}] {c.name}: {c.content[:250]}\n---\n"
    
    eval_prompt += (
        f"\nCRITICAL: Strict filter. Select only indices DIRECTLY RELEVANT to: '{prompt}'. "
        "Ignore generic info or wrong products. If none relevant, return [].\n"
        "JSON ONLY: {\"relevant_indices\": [int, ...]}"
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
                    "options": {"temperature": 0, "num_ctx": 4096}
                },
                timeout=config.LLM_TIMEOUT
            )
            data = resp.json().get("message", {}).get("content", "{}")
            indices = json.loads(data).get("relevant_indices", [])
            
            with open(log_path, "a", encoding="utf-8") as f:
                if not indices:
                    f.write("Context Evaluator: No relevant context found in database.\n")
                    return ""
                
                f.write(f"Context Evaluator: Selected {len(indices)} relevant entries.\n")
                full_context = "PRIOR KNOWLEDGE:\n"
                for i in indices:
                    if 0 <= int(i) < len(contexts):
                        c = contexts[int(i)]
                        f.write(f" - Using Context: {c.name}\n")
                        full_context += f"--- {c.name} ---\n{c.content}\n\n"
                return full_context + "USE THIS TO INFORM YOUR ACTIONS.\n\n"
    except Exception as e:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"Context pruning failed: {e}\n")
        return ""
