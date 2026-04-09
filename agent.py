import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import utils
from browser_use import Agent, BrowserSession, ChatOllama
import database
from database import SessionLocal, Task as DBTask, Output, Context
from datetime import datetime, timezone

async def run_agent_task(task_id: int, prompt: str):
    db = SessionLocal()
    task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not task:
        db.close()
        return

    task.status = "RUNNING"
    task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    # Clean up lingering headless Chrome processes on Windows to prevent CDP timeout errors
    if sys.platform == 'win32':
        import subprocess
        try:
            # Safely terminate only the chrome processes running in headless mode
            # This prevents killing the user's regular browser Windows
            kill_cmd = 'wmic process where "name=\'chrome.exe\' and commandline like \'%--headless%\'" call terminate'
            subprocess.run(kill_cmd, shell=True, capture_output=True)
        except Exception as e:
            print(f"Warning: Failed to clean up headless chrome processes: {e}")

    from browser_use.llm.ollama.serializer import OllamaMessageSerializer
    from browser_use.llm.views import ChatInvokeCompletion
    class JsonStrippingChatOllama(ChatOllama):
        async def ainvoke(self, messages, output_format=None, **kwargs):
            try:
                # Let's call the original method. If it succeeds natively, return immediately.
                return await super().ainvoke(messages, output_format=output_format, **kwargs)
            except Exception as e:
                if output_format is not None:
                    # If strict Pydantic parsing failed, retry natively as a raw string to strip markdown blocks
                    ollama_messages = OllamaMessageSerializer.serialize_messages(messages)
                    response = await self.get_client().chat(
                        model=self.model,
                        messages=ollama_messages,
                        format=output_format.model_json_schema(),
                        options=self.ollama_options,
                    )
                    content = response.message.content or ''
                    # Aggressive markdown stripping
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    
                    parsed = output_format.model_validate_json(content)
                    return ChatInvokeCompletion(completion=parsed, usage=None)
                raise e

    llm = JsonStrippingChatOllama(
        model="gemma4:26b", 
        timeout=300, # 5 minutes maximum for slow heavy context parsing
        ollama_options={
            "temperature": 0, 
            "num_ctx": 32768,
            "num_thread": 8
        }
    )
    browser = BrowserSession(
        headless=True,
        disable_security=True,
        enable_default_extensions=False,
        minimum_wait_page_load_time=3.0,
        wait_for_network_idle_page_load_time=3.0,
        highlight_elements=True,
        paint_order_filtering=True,
        user_data_dir=".browser_session_web",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-background-networking",
            "--disable-default-apps",
            "--window-size=1920,1080", 
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ],
    )
    
    try:
        # Fetch contexts
        contexts = db.query(Context).all()
        context_str = ""
        if contexts:
            eval_prompt = (
                f"Identify which of the following contexts are directly relevant to the user task.\n\n"
                f"USER TASK: {prompt}\n\n"
                f"AVAILABLE CONTEXTS:\n"
            )
            for i, c in enumerate(contexts):
                content_preview = c.content[:500] + ("..." if len(c.content) > 500 else "")
                eval_prompt += f"[{i}] {c.name}: {content_preview}\n"
                
            eval_prompt += (
                "\nINSTRUCTIONS:\n"
                "1. Select the indices of any contexts that provide relevant background, instructions, or prior knowledge for the task.\n"
                "2. Output ONLY a valid JSON object with the key 'relevant_indices' containing a list of integers.\n"
                "3. Example: {\"relevant_indices\": [0, 2]}\n"
                "4. Do NOT include any other text, reasoning, or explanations."
                "5. If nothing is relevant, return exactly: {\"relevant_indices\": []}"
            )
            
            try:
                import requests
                # Bypass LangChain abstraction completely and hit the local API directly using JSON format
                ollama_resp = requests.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": "gemma4:26b",
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a context selection AI. You MUST output ONLY a valid JSON object with the exact key 'relevant_indices' containing a list of integers."
                            },
                            {
                                "role": "user",
                                "content": eval_prompt
                            }
                        ],
                        "stream": False,
                        "format": "json",
                        "options": {
                            "temperature": 0.0
                        }
                    },
                    timeout=120
                )
                ollama_resp.raise_for_status()
                resp_data = ollama_resp.json()
                resp_text = resp_data.get("message", {}).get("content", "").strip()
                print(f"DEBUG - Ollama Context Selection Output: {resp_text}")
                
                import json
                try:
                    parsed_json = json.loads(resp_text)
                    relevant_indices = parsed_json.get("relevant_indices", [])
                    
                    if not isinstance(relevant_indices, list):
                        if relevant_indices is not None:
                            relevant_indices = [relevant_indices]
                        else:
                            relevant_indices = []
                            
                    print(f"DEBUG - Parsed indices: {relevant_indices}")
                    
                    # Filter out any invalid numbers
                    valid_indices = []
                    for i in relevant_indices:
                        if isinstance(i, list):
                            for sub_i in i:
                                try:
                                    val = int(sub_i)
                                    if 0 <= val < len(contexts):
                                        valid_indices.append(val)
                                except (ValueError, TypeError):
                                    pass
                        else:
                            try:
                                val = int(i)
                                if 0 <= val < len(contexts):
                                    valid_indices.append(val)
                            except (ValueError, TypeError):
                                pass
                    relevant_indices = list(set(valid_indices))
                except Exception as e:
                    print(f"DEBUG - Error parsing JSON or extracting indices: {e}")
                    relevant_indices = []
                
                if relevant_indices:
                    print(f"DEBUG - Injecting {len(relevant_indices)} context(s) into task.")
                    context_str = "RELEVANT CONTEXTS AND PRIOR KNOWLEDGE:\n"
                    for i in relevant_indices:
                        c = contexts[int(i)]
                        print(f"  - Context: {c.name}")
                        context_str += f"--- {c.name} ---\n{c.content}\n\n"
                    context_str += "PLEASE USE THE ABOVE CONTEXTS TO INFORM YOUR ACTIONS FOR THE FOLLOWING TASK.\n\n"
                else:
                    print("DEBUG - No relevant contexts found by LLM.")
            except Exception as e:
                print(f"Failed to evaluate contexts with LLM: {e}. Injecting NO contexts as fallback to avoid noise.")
                # We do NOT inject all contexts as fallback, as that creates massive noise issues.
                context_str = ""

        anti_hallucination_prompt = (
            "[STRICT_PROTOCOL]\n"
            "- ERROR_POLICY: On tool_fail, DO NOT hallucinate. Retry or Report.\n"
            "- VERACITY: 0% fabrication. Use ONLY visible page text.\n"
            "- OUTPUT: RAW JSON ONLY. No markdown. No preambles.\n"
            "- FORMAT: Start='{', End='}'. Ensure valid closing.\n"
            "- LENS: Only extract what is explicitly on screen.\n"
            "[/STRICT_PROTOCOL]"
        )

        full_task = context_str + "USER TASK: " + prompt

        agent = Agent(
            task=full_task,
            llm=llm,
            browser=browser,
            use_vision=False,
            max_steps=50,
            max_failures=10,
            llm_timeout=300, # Raise the LLM timeout threshold natively in browser-use
            step_timeout=600, # Safely increase step timeout
            extend_system_message=anti_hallucination_prompt,
        )
        
        history = await agent.run()
        final_res = history.final_result()
        if not final_res and history.history:
            last_step = history.history[-1]
            if last_step.result:
                final_res = last_step.result[-1].extracted_content
                
        is_success = True
        
        # history.is_successful() returns True if success, False if explicitly failed by judge, 
        # and None if missing/incomplete. So we strictly check for `False`.
        if hasattr(history, 'is_successful') and history.is_successful() is False:
            is_success = False
            
        if hasattr(history, 'has_errors') and history.has_errors():
            is_success = False
            
        if hasattr(history, 'is_done') and not history.is_done():
            is_success = False
            
        # Often the AI returns a valid final extraction but forgets to set 'success=True'
        # So if we have a valid final_res, we only mark it failed if it explicitly reported errors.
        if not final_res:
            final_res = "No result extracted"
            is_success = False
            
        # If the final result literally says it failed or couldn't find it, consider it a failure.
        if final_res and isinstance(final_res, str):
            lower_res = final_res.lower()
            if "i failed" in lower_res or "could not find" in lower_res or "unable to" in lower_res:
                is_success = False
            
        # Save output to DB
        output = Output(task_id=task_id, content=final_res)
        db.add(output)
        
        if is_success:
            task.status = "COMPLETED"
        else:
            task.status = "FAILED"
            
        db.commit()
        
    except Exception as e:
        task.status = "FAILED"
        print(f"Agent failed: {e}")
        db.commit()
    finally:
        await browser.stop()
        db.close()

if __name__ == "__main__":
    import asyncio
    import sys
    if len(sys.argv) > 2:
        task_id = int(sys.argv[1])
        prompt = sys.argv[2]
        asyncio.run(run_agent_task(task_id, prompt))
    else:
        # Fallback for testing
        asyncio.run(run_agent_task(1, "Search for ice cream deals on Safeway"))