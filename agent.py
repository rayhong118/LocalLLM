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

        if output_format is None:
            return ChatInvokeCompletion(completion=content, usage=None)

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
                # AgentOutput in v0.12.6+ forbids extra fields like 'current_state' and expects flattened fields.
                wrapped_data = {
                    "evaluation_previous_goal": "Extracted direct text answer.",
                    "memory": "Model provided direct text instead of JSON.",
                    "next_goal": "Finish",
                    "action": [{"done": {"text": text[:1000]}}]
                }
                return schema.model_validate(wrapped_data)
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
                if "action" in data:
                    if isinstance(data["action"], dict):
                        data["action"] = [data["action"]]
                    elif isinstance(data["action"], str):
                        data["action"] = [{"done": {"text": data["action"]}}]
                
                # In v0.12.6, AgentOutput flattens these fields. 
                # If the model tried to output a nested "current_state" object, flatten it.
                if "current_state" in data and isinstance(data["current_state"], dict):
                    cs = data.pop("current_state")
                    for k, v in cs.items():
                        if k in ["evaluation_previous_goal", "memory", "next_goal", "thinking"]:
                            data[k] = v
                
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


async def get_relevant_context_str(db, prompt: str) -> str:
    """Uses LLM to prune irrelevant contexts to save token window space."""
    contexts = db.query(Context).all()
    if not contexts:
        return ""

    eval_prompt = f"TASK: {prompt}\n\nSELECT RELEVANT CONTEXT INDICES:\n"
    for i, c in enumerate(contexts):
        eval_prompt += f"[{i}] {c.name}: {c.content[:200]}...\n"
    
    eval_prompt += "\nRespond with valid JSON: {\"relevant_indices\": [int, ...]}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": eval_prompt}],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0, "num_ctx": 2048}
                },
                timeout=config.LLM_TIMEOUT
            )
            data = resp.json().get("message", {}).get("content", "{}")
            indices = json.loads(data).get("relevant_indices", [])
            
            if not indices:
                return ""
            
            full_context = "PRIOR KNOWLEDGE:\n"
            for i in indices:
                if 0 <= int(i) < len(contexts):
                    c = contexts[int(i)]
                    full_context += f"--- {c.name} ---\n{c.content}\n\n"
            return full_context + "USE THIS TO INFORM YOUR ACTIONS.\n\n"
    except Exception as e:
        print(f"Context pruning failed: {e}")
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
    logging.getLogger().addHandler(file_handler)
    
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

    browser = BrowserSession(
        headless=config.HEADLESS,
        disable_security=True,
        minimum_wait_page_load_time=config.BROWSER_WAIT_TIME,
        wait_for_network_idle_page_load_time=config.BROWSER_WAIT_TIME,
        user_data_dir=".browser_session_web",
        args=["--disable-blink-features=AutomationControlled", "--window-size=1920,1080"],
    )

    try:
        # Context building
        context_str = await get_relevant_context_str(db, prompt)
        
        # System Instructions
        protocol = (
            "### JSON PROTOCOL ###\n"
            "- Output valid RAW JSON only.\n"
            "- 'action' MUST be a LIST: [{\"click_element\": ...}]\n"
            "- ONLY use visible text from elements.\n"
            "### END PROTOCOL ###"
        )

        agent = Agent(
            task=context_str + "USER TASK: " + prompt,
            llm=llm,
            browser=browser,
            use_vision=False,
            max_steps=config.MAX_STEPS,
            max_failures=config.MAX_FAILURES,
            llm_timeout=config.LLM_TIMEOUT,
            step_timeout=600,
            extend_system_message=protocol,
            max_actions_per_step=1
        )
        
        history = await agent.run()
        
        # Determine Success/Result
        final_res = history.final_result() or "No result extracted"
        if final_res == "No result extracted" and history.history:
            last_match = next((h.result[-1].extracted_content for h in reversed(history.history) if h.result), None)
            if last_match: final_res = last_match
            
        # Robust Success Logic:
        # 1. Must be explicitly 'done'
        # 2. Must not be explicitly 'failed'
        # 3. Must not have any registered errors in history
        is_success = history.is_done() and history.is_successful() is not False
        if history.has_errors():
            is_success = False
            
        # 4. Result must not contain common failure strings (case-insensitive)
        fail_keywords = ["i failed", "could not find", "unable to", "terminated", "no task results"]
        lower_res = final_res.lower()
        if any(kw in lower_res for kw in fail_keywords):
            is_success = False

        # Persist results
        db.add(Output(task_id=task_id, content=final_res))
        task.status = "COMPLETED" if is_success else "FAILED"
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"Final Success State: {is_success}\n")
            f.write(f"Final Result: {final_res[:500]}...\n")
        
        db.commit()

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