import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import utils
from browser_use import Agent, BrowserSession, ChatOllama
import database
from database import SessionLocal, Task as DBTask, Output, Context
from datetime import datetime

async def run_agent_task(task_id: int, prompt: str):
    db = SessionLocal()
    task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not task:
        db.close()
        return

    task.status = "RUNNING"
    task.started_at = datetime.utcnow()
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

    llm = ChatOllama(model="qwen3.5-32k")
    browser = BrowserSession(
        headless=True,
        disable_security=True,
        enable_default_extensions=False,
        minimum_wait_page_load_time=3,
        wait_for_network_idle_page_load_time=5,
        user_data_dir=".browser_session_web",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    
    try:
        # Fetch contexts
        contexts = db.query(Context).all()
        context_str = ""
        if contexts:
            eval_prompt = f"User task: {prompt}\n\nAvailable contexts:\n"
            for i, c in enumerate(contexts):
                content_preview = c.content[:500] + ("..." if len(c.content) > 500 else "")
                eval_prompt += f"[{i}] {c.name}: {content_preview}\n"
                
            eval_prompt += "\nRespond ONLY with a comma-separated list of the numbers (e.g. 0, 2) of the most relevant contexts for the task. If none are relevant, reply with 'NONE'."
            
            try:
                import requests
                # Bypass LangChain abstraction completely and hit the local API directly
                ollama_resp = requests.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": "qwen3.5-32k",
                        "prompt": eval_prompt,
                        "stream": False
                    },
                    timeout=30
                )
                ollama_resp.raise_for_status()
                resp_text = ollama_resp.json().get("response", "")
                
                # Extract numbers correctly (e.g. '0', '1, 2')
                import re
                numbers_found = re.findall(r'\b\d+\b', resp_text)
                
                relevant_indices = []
                for i, c in enumerate(contexts):
                    if str(i) in numbers_found:
                        relevant_indices.append(i)
                        
                if relevant_indices:
                    context_str = "RELEVANT CONTEXTS AND PRIOR KNOWLEDGE:\n"
                    for i in relevant_indices:
                        c = contexts[i]
                        context_str += f"--- {c.name} ---\n{c.content}\n\n"
                    context_str += "PLEASE USE THE ABOVE CONTEXTS TO INFORM YOUR ACTIONS FOR THE FOLLOWING TASK.\n\n"
            except Exception as e:
                print(f"Failed to evaluate contexts with LLM: {e}. Injecting all contexts as fallback.")
                context_str = "RELEVANT CONTEXTS AND PRIOR KNOWLEDGE:\n"
                for c in contexts:
                    context_str += f"--- {c.name} ---\n{c.content}\n\n"
                context_str += "PLEASE USE THE ABOVE CONTEXTS TO INFORM YOUR ACTIONS FOR THE FOLLOWING TASK.\n\n"

        anti_hallucination_prompt = (
            "\n\n=== CRITICAL INSTRUCTIONS ===\n"
            "1. IF A TOOL FAILS (e.g., the extract tool), YOU MUST NOT invent or hallucinate information.\n"
            "2. You must either retry the tool, try a different search method, or explicitly report the error.\n"
            "3. DO NOT output fabricated news, events, or facts under any circumstances. Rely EXACTLY on the text visible on the page.\n"
            "4. Make sure your extraction output is completely finished and not cut off.\n"
            "============================="
        )

        full_task = context_str + "USER TASK: " + prompt + anti_hallucination_prompt

        agent = Agent(
            task=full_task,
            llm=llm,
            browser=browser,
            use_vision=False,
            max_steps=50
        )
        
        history = await agent.run()
        final_res = history.final_result()
        if not final_res and history.history:
            last_step = history.history[-1]
            if last_step.result:
                final_res = last_step.result[-1].extracted_content
                
        is_success = True
        
        # Check if the built-in browser-use evaluator/judge marked it as failed
        if hasattr(history, 'is_successful') and not history.is_successful():
            is_success = False

        if not final_res:
            final_res = "No result extracted"
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
    # For testing: run manually
    asyncio.run(run_agent_task(1, "Search for ice cream deals on Safeway"))