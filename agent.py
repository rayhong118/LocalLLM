import asyncio
import sys
import json
import httpx
import re
import logging
from datetime import datetime, timezone
from typing import List, Optional, Any

# browser-use imports
from browser_use import Agent, BrowserSession, ChatOllama
from browser_use.llm.ollama.serializer import OllamaMessageSerializer
from browser_use.llm.views import ChatInvokeCompletion
from browser_use.llm.exceptions import ModelProviderError

# Local imports
import config
import database
from database import SessionLocal, Task as DBTask, Output, Context

# Set up logging to decrease noise but keep important events
logging.getLogger('browser_use').setLevel(logging.WARNING)

if config.IS_WINDOWS:
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

class JsonStrippingChatOllama(ChatOllama):
    """Refined ChatOllama wrapper with multi-stage JSON repair and Qwen/Gemma residency."""
    
    async def ainvoke(self, messages, output_format=None, **kwargs) -> ChatInvokeCompletion:
        ollama_messages = OllamaMessageSerializer.serialize_messages(messages)
        format_param = output_format.model_json_schema() if output_format else None
        
        async def call_llm(fmt):
            try:
                resp = await self.get_client().chat(
                    model=self.model,
                    messages=ollama_messages,
                    format=fmt,
                    options=self.ollama_options,
                )
                return resp.message.content or ''
            except Exception as e:
                print(f"DEBUG - LLM Call Error: {e}")
                return None

        # Try strict schema first, then generic JSON mode if it fails/returns empty
        content = await call_llm(format_param)
        if not content and format_param:
            content = await call_llm("json")
        
        if not content:
            raise ModelProviderError(message="LLM returned no content.", model=self.name)

        # Pre-processing: Strip reasoning blocks and common markdown wrappers
        content = self._clean_raw_content(content)

        # Repetition Loop Safety: Check for massive recursive loops (e.g. "and a 'a' and a 'a'")
        # If we detect a character/word repeating more than 50 times, truncate it.
        for seq in re.findall(r'(\s+\w+){3,}', content):
             if content.count(seq) > 20:
                 print("DEBUG - Detected repetition loop. Truncating.")
                 content = content.split(seq)[0] + " [TRUNCATED LOOP]"
                 break

        # Assistant Fallback: If model starts being a "helpful assistant" with tables instead of JSON, 
        # we treat it as a failure unless it contains a '{'.
        if '{' not in content and ('|' in content or '###' in content or '1.' in content):
             print("DEBUG - Detected helpful assistant table/list instead of JSON.")
             # No braces found, let the Text-to-Done wrapper handle it or fail.

        # Parsing Pipeline
        # 1. Direct validation
        try:
            return ChatInvokeCompletion(completion=output_format.model_validate_json(content), usage=None)
        except Exception:
            pass

        # 2. Heuristic extraction and repair
        repaired = self._repair_json(content, output_format)
        if repaired:
            return ChatInvokeCompletion(completion=repaired, usage=None)

        # 3. Persistent Failure Logging
        self._log_failure_trace(content)
        raise ModelProviderError(
            message=f"Failed to parse model output. Raw trace saved. Snippet: {content[:200]}",
            model=self.name
        )

    def _clean_raw_content(self, text: str) -> str:
        """Removes non-JSON blocks like <thought> or markdown wrappers."""
        for tag in ['thought', 'reasoning', 'thinking']:
            if f'<{tag}>' in text:
                text = text.split(f'</{tag}>')[-1]
        
        # Clean specific internal tokens or broken prefixes
        text = text.replace('---<channel|>', '')
        text = text.replace('---', '')

        if '```json' in text:
            text = text.split('```json')[-1].split('```')[0]
        return text.strip()

    def _repair_json(self, text: str, schema: Any) -> Optional[Any]:
        """Attempts to extract and fix malformed JSON strings."""
        # Find brace-bounded content
        start, end = text.find('{'), text.rfind('}')
        
        # Last resort fallback: If no JSON braces at all, wrap the entire text as a 'done' action.
        # This occurs when local models (like Gemma) blurt out the answer directly.
        if start == -1 or end == -1:
            try:
                # ONLY wrap if the model seems to have reached a terminal conclusion.
                # Avoid common mid-step words like "successfully" or "action".
                terminal_keywords = ["final result:", "task complete", "i have found", "failure:", "cannot find", "summary of results", "the current", "value:", "based on the"]
                if any(kw in text.lower() for kw in terminal_keywords):
                    wrapped_data = {
                        "thinking": f"Extracted text answer from fallback: {text[:200]}...",
                        "evaluation_previous_goal": "Extraction via text fallback",
                        "memory": "Model provided a terminal text answer.",
                        "next_goal": "Finish",
                        "action": [{"done": {"text": text[:2000]}}]
                    }
                    return schema.model_validate(wrapped_data)
                
                print("DEBUG - Text found but doesn't look like a final answer. Rejecting to force retry.")
                return None
            except Exception as e:
                print(f"DEBUG - Text-to-Done wrapper failed: {e}")
                return None
        
        raw_json = text[start:end+1]
        
        # Try finding valid blocks via regex if simple strip fails
        blocks = re.findall(r'\{.*\}', text, re.DOTALL)
        for block in reversed(blocks):
            try:
                # Basic cleanup: remove trailing commas
                cleaned = re.sub(r',\s*([\]}])', r'\1', block)
                
                # Structural fix: wrap single 'action' in list if needed
                data = json.loads(cleaned)
                
                # If the model returned a raw list instead of an object
                if isinstance(data, list):
                    data = {"action": data}

                # 4. Hallucination Guard: Catch 'plan' before it gets wrapped as an action
                for hallucination in ["plan", "action_input"]:
                    if hallucination in data:
                        val = str(data.pop(hallucination))
                        data["thinking"] = (data.get("thinking", "") + "\n" + val).strip()
                
                # If 'action' itself is a string like "plan", clear it.
                if isinstance(data.get("action"), str) and data["action"].lower() in ["plan", "none", "null"]:
                    data["action"] = []

                if "action" in data:
                    if isinstance(data["action"], dict):
                        data["action"] = [data["action"]]
                    elif isinstance(data["action"], str):
                        # ONLY wrap as 'done' if it's NOT a hallucinated thinking word
                        lower_act = data["action"].lower()
                        if not any(lower_act.startswith(halluc) for halluc in ["plan", "thinking", "action", "step", "goal"]):
                            data["action"] = [{"done": {"text": data["action"]}}]
                        else:
                            data["action"] = []
                
                # In v0.12.6, AgentOutput flattens these fields. 
                # If the model tried to output a nested "current_state" object, flatten it.
                if "current_state" in data and isinstance(data["current_state"], dict):
                    cs = data.pop("current_state")
                    for k, v in cs.items():
                        if k in ["evaluation_previous_goal", "memory", "next_goal", "thinking"]:
                            data[k] = v
                
                # Global Action Normalizer: Fixes mapping for common "hallucinated" action names
                if "action" in data and isinstance(data["action"], list):
                    for act in data["action"]:
                        # 1. Map "type" -> "type_text"
                        if "type" in act and "type_text" not in act:
                            act["type_text"] = {"text": act.pop("type"), "index": act.pop("index", 0)}
                        # 2. Map "press" or "press_key" -> "key_combination"
                        if ("press" in act or "press_key" in act) and "key_combination" not in act:
                            key = act.pop("press", act.pop("press_key", None))
                            act["key_combination"] = {"key_combination": key}
                        # 3. Ensure mandatory fields exist (default to 0 if model misses index)
                        for field in ["click_element", "hover_element", "scroll_to_element"]:
                            if field in act and isinstance(act[field], dict) and "index" not in act[field]:
                                act[field]["index"] = 0
                
                return schema.model_validate(data)
            except Exception:
                continue
        return None

    def _log_failure_trace(self, content: str):
        # We now use the task-specific log file if available, otherwise fallback
        log_path = getattr(self, "log_path", "format_error_trace.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- FAILED LLM OUTPUT {datetime.now()} ---\n{content}\n--- END ---\n")


def cleanup_headless_chrome():
    """Safety cleanup for Windows to prevent CDP zombie processes."""
    if config.IS_WINDOWS:
        import subprocess
        try:
            kill_cmd = 'wmic process where "name=\'chrome.exe\' and commandline like \'%--headless%\'" call terminate'
            subprocess.run(kill_cmd, shell=True, capture_output=True)
        except Exception as e:
            print(f"Warning: Headless cleanup failed: {e}")


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
                    "options": {"temperature": 0, "num_ctx": 4096} # Slightly larger ctx to see more entries
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


async def run_agent_task(task_id: int, prompt: str):
    # Ensure logs directory exists
    import os
    if not os.path.exists("logs"):
        os.makedirs("logs")
    
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"logs/task_{task_id}_{run_timestamp}.log"
    
    # Configure logging to write to this file
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    file_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(file_handler)
    logging.getLogger('browser_use').setLevel(logging.INFO)
    logging.getLogger().setLevel(logging.INFO)
    
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"=== AGENT RUN STARTED ===\nTask ID: {task_id}\nPrompt: {prompt}\nTimestamp: {run_timestamp}\n\n")

    db = SessionLocal()
    task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not task:
        db.close()
        return

    task.status = "RUNNING"
    task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    cleanup_headless_chrome()

    # Core components
    llm = JsonStrippingChatOllama(
        model=config.LLM_MODEL,
        timeout=config.LLM_TIMEOUT,
        ollama_options={
            "temperature": config.TEMPERATURE,
            "num_ctx": config.CONTEXT_WINDOW,
            "num_predict": 4096,
            "num_thread": 8
        }
    )
    llm.log_path = log_path # Attach log path for repair traces

    # Attach metadata for repair traces and pre-validate model
    llm.log_path = log_path 

    # Advanced Browser Config for VRAM/Speed
    browser = BrowserSession(
        headless=config.HEADLESS,
        disable_security=True,
        minimum_wait_page_load_time=config.BROWSER_WAIT_TIME,
        wait_for_network_idle_page_load_time=config.BROWSER_WAIT_TIME,
        user_data_dir=".browser_session_web",
        args=[
            "--disable-blink-features=AutomationControlled", 
            "--window-size=1280,720", # Smaller window = smaller DOM tree
            "--disable-extensions",
            "--mute-audio",
            "--no-sandbox",
            "--disable-dev-shm-usage" # Stability on limited resource systems
        ],
    )

    try:
        # Context building
        context_str = await get_relevant_context_str(db, prompt, log_path)
        
        # Caveman Optimized Protocol with Anti-Hallucination
        full_protocol = (
            f"{context_str}\n"
            "### MANDATORY_OPERATIONAL_STANCE ###\n"
            "1. NO_PREMATURE_FINISH: Forbidden from finishing with category summaries. "
            "You MUST find specific items with prices. If results are hidden, CLICK 'Show More'.\n"
            "2. SEARCH_PRIORITY: Use search bar IMMEDIATELY. Type query and hit Enter. Do NOT browse categories first.\n"
            "3. NO_PLANNING: Outputting 'plan' is a FATAL ERROR. Take one concrete browser action (click/type/scroll).\n"
            "4. DEAL_HUNTER: specifically look for 'Club Price', 'Buy 1 Get 1', or 'Digital Coupon' for grocery sites.\n"
            "5. VERACITY: Report ONLY facts visible on LIVE SCREEN. No assumptions.\n\n"
            "### JSON_OUTPUT_SCHEMA ###\n"
            "- CRITICAL: Output RAW JSON ONLY. No markdown block wrappers. No text outside braces.\n"
            "- FIELDS: \"thinking\" (REASONING), \"action\" (LIST OF COMMANDS).\n"
            "- FORBIDDEN: Do NOT use keys like 'plan' or 'action_input'. Use 'action' list only.\n"
            "- CRITICAL: If you reach the final answer, use 'done' action IMMEDIATELY within the JSON.\n"
            "- EXAMPLE:\n"
            "{\n"
            "  \"thinking\": \"Need to search for chocolate ice cream.\",\n"
            "  \"action\": [{\"type_text\": {\"index\": 12, \"text\": \"chocolate ice cream\"}}, {\"key_combination\": {\"key_combination\": \"Enter\"}}]\n"
            "}\n\n"
            f"### FINAL_GOAL: {prompt} ###\n"
            "### VERIFIED_URL: https://www.safeway.com ###\n"
            "### END_PROTOCOL ###"
        )

        agent = Agent(
            task=prompt, # Keep purely as the goal
            llm=llm,
            browser=browser,
            use_vision=False,
            max_steps=config.MAX_STEPS,
            max_failures=config.MAX_FAILURES,
            llm_timeout=config.LLM_TIMEOUT,
            step_timeout=600,
            extend_system_message=full_protocol,
            max_actions_per_step=1,
            # CRITICAL: Prune DOM to reduce VRAM use and increase LLM focus
            include_attributes=["title", "type", "name", "role", "aria-label", "placeholder", "value"]
        )
        
        history = await agent.run()
        
        # Determine Success/Result
        final_res = history.final_result() or "No result extracted"
        if final_res == "No result extracted" and history.history:
            last_match = next((h.result[-1].extracted_content for h in reversed(history.history) if h.result), None)
            if last_match: final_res = last_match
            
        # Robust Success Logic:
        # 1. Must be explicitly 'done' OR have extracted content
        is_done = history.is_done() or (final_res and final_res != "No result extracted")
        is_success = is_done and history.is_successful() is not False
        
        # 2. Filter out non-critical errors (e.g. pipe errors, session timeouts at the end)
        if history.has_errors():
            critical_errors = [
                e for e in history.errors() 
                if "closed pipe" not in str(e).lower() 
                and "resourcewarning" not in str(e).lower()
                and "connection closed" not in str(e).lower()
            ]
            if critical_errors:
                is_success = False
            
        # 3. Result must not contain common failure strings (case-insensitive)
        fail_keywords = ["i failed", "could not find", "unable to", "terminated", "no task results", "fail", "hallucination", "plan..."]
        lower_res = final_res.lower()
        if any(kw in lower_res for kw in fail_keywords) or len(lower_res) < 10:
            is_success = False
            
        # 4. Multilingual Goal Validation: Result MUST contain at least one core keyword from the prompt
        # Extract both English words and Chinese characters as potential keywords
        stop_words = {
            'look', 'search', 'find', 'navigate', 'click', 'check', 'website', 'page', 
            'following', 'today', 'items', 'for', 'the', 'and', 'with', 'from', 
            'that', 'this', 'these', 'those', 'list', 'show', 'give', 'tell'
        }
        en_keywords = [w for w in re.findall(r'[a-z]{3,}', prompt.lower()) if w not in stop_words]
        cn_keywords = re.findall(r'[\u4e00-\u9fff]{2,}', prompt) # Only 2+ char Chinese words
        
        core_keywords = sorted(list(set(en_keywords + cn_keywords)), key=len, reverse=True)
        
        if is_success and core_keywords:
            # If the goal is VIX, we might want to check for numbers as well
            if 'vix' in prompt.lower() and not re.search(r'\d+', lower_res):
                is_success = False
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Validation Error: VIX goal but no numerical values found in result.\n")

            if not any(kw in lower_res for kw in core_keywords):
                msg = f"Validation Error: Result does not mention core keywords {core_keywords}"
                try:
                    print(f"DEBUG - {msg}")
                except UnicodeEncodeError:
                    print("DEBUG - Validation Error (encoding suppressed)")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"{msg}\n")
                is_success = False

        if not is_success:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Success Logic breakdown:\n")
                f.write(f" - is_done: {is_done}\n")
                f.write(f" - history.is_successful(): {history.is_successful()}\n")
                if history.has_errors():
                    f.write(f" - history.errors(): {history.errors()}\n")
                if any(kw in lower_res for kw in fail_keywords):
                    f.write(f" - Failure keyword detected in result.\n")

        # Persist results with retry safety
        try:
            db.add(Output(task_id=task_id, content=final_res))
            task.status = "COMPLETED" if is_success else "FAILED"
            
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Final Success State: {is_success}\n")
                f.write(f"Final Result: {final_res[:2000]}...\n")
            
            db.commit()
        except Exception as db_err:
            print(f"Database commit failed: {db_err}")
            db.rollback()

    except Exception as e:
        task.status = "FAILED"
        msg = f"CRITICAL ERROR: {str(e)}"
        db.add(Output(task_id=task_id, content=msg))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\nFATAL AGENT ERROR: {e}\n")
        print(f"Agent failed: {e}")
        db.commit()
    finally:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n=== AGENT RUN FINISHED ===\n")
        logging.getLogger().removeHandler(file_handler)
        await browser.stop()
        db.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        asyncio.run(run_agent_task(int(sys.argv[1]), sys.argv[2]))
    else:
        asyncio.run(run_agent_task(1, "Search for latest news on Ollama"))