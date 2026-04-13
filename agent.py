import asyncio
import sys
import re
import logging
from datetime import datetime, timezone

# browser-use imports
from browser_use import Agent, BrowserSession

# Local imports
import config
from database import SessionLocal, Task as DBTask, Output
from llm_wrapper import JsonStrippingChatOllama
from context_manager import get_relevant_context_str
from browser_utils import cleanup_headless_chrome

# Set up logging
logging.getLogger('browser_use').setLevel(logging.WARNING)

if config.IS_WINDOWS:
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def run_agent_task(task_id: int, prompt: str):
    # Ensure logs directory exists
    import os
    if not os.path.exists("logs"):
        os.makedirs("logs")
    
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"logs/{run_timestamp}_task_{task_id}.log"
    
    # Configure logging
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

    llm = JsonStrippingChatOllama(
        model=config.LLM_MODEL,
        timeout=config.LLM_TIMEOUT,
        ollama_options={
            "temperature": config.TEMPERATURE,
            "num_ctx": config.CONTEXT_WINDOW,
            "num_predict": 4096,
            "num_thread": 8,
            "repeat_penalty": 1.2
        }
    )
    llm.log_path = log_path 

    browser = BrowserSession(
        headless=config.HEADLESS,
        disable_security=True,
        minimum_wait_page_load_time=config.BROWSER_WAIT_TIME,
        wait_for_network_idle_page_load_time=config.BROWSER_WAIT_TIME,
        user_data_dir=".browser_session_web",
        args=[
            "--disable-blink-features=AutomationControlled", 
            "--window-size=1280,720",
            "--disable-extensions",
            "--mute-audio",
            "--no-sandbox",
            "--disable-dev-shm-usage"
        ],
    )

    try:
        from skills import controller
        context_str = await get_relevant_context_str(db, prompt, log_path)
        
        # CAVEMAN PROTOCOL (Ultra-Terse)
        full_protocol = (
            f"{context_str}\n"
            "### RULES ###\n"
            "1. JSON ONLY. NO CHAT. NO MARKDOWN.\n"
            "2. STEP 1: MAKE PLAN IN 'memory' FIELD.\n"
            "3. USE SKILLS: 'smart_search' for any search. 'click_element_by_text' for buttons.\n"
            "4. NO HALLUCINATIONS. DON'T REPEAT PLAN.\n\n"
            "### SCHEMA ###\n"
            "{\"thinking\": \"Short logic\", \"memory\": \"Plan status\", \"action\": []}\n"
            f"### GOAL: {prompt} ###"
        )

        agent = Agent(
            task=prompt,
            llm=llm,
            browser=browser,
            controller=controller, # Integrated Skills
            use_vision=False,
            max_steps=config.MAX_STEPS,
            max_failures=config.MAX_FAILURES,
            llm_timeout=config.LLM_TIMEOUT,
            step_timeout=600,
            extend_system_message=full_protocol,
            max_actions_per_step=1,
            include_attributes=["title", "type", "name", "role", "aria-label", "placeholder", "value"]
        )
        
        history = await agent.run()
        
        # Log Step-by-Step History
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n--- STEP-BY-STEP EXECUTION LOG ---\n")
            for i, h in enumerate(history.history):
                f.write(f"\n[Step {i+1}]\n")
                if h.model_output:
                    thinking = getattr(h.model_output, "thinking", "No thinking provided")
                    memory = getattr(h.model_output, "memory", None)
                    f.write(f"THINKING: {thinking}\n")
                    if memory:
                        f.write(f"MEMORY: {memory}\n")
                    
                    if h.model_output.action:
                        for act_idx, act in enumerate(h.model_output.action):
                            act_json = act.model_dump_json(exclude_none=True)
                            f.write(f"ACTION {act_idx + 1}: {act_json}\n")
                
                if h.result:
                    for res_idx, res in enumerate(h.result):
                        status = "SUCCESS" if not res.is_done and not getattr(res, "error", None) else "FINISH/ERROR"
                        content = res.extracted_content or res.error or "Action concluded."
                        f.write(f"RESULT {res_idx + 1} ({status}): {content[:500]}\n")
            f.write("\n--- END OF EXECUTION LOG ---\n\n")
        
        # Determine Success/Result
        final_res = history.final_result() or "No result extracted"
        if final_res == "No result extracted" and history.history:
            last_match = next((h.result[-1].extracted_content for h in reversed(history.history) if h.result), None)
            if last_match: final_res = last_match
            
        is_done = history.is_done() or (final_res and final_res != "No result extracted")
        is_success = is_done and history.is_successful() is not False
        
        if history.has_errors():
            critical_errors = [e for e in history.errors() if not any(x in str(e).lower() for x in ["closed pipe", "resourcewarning", "connection closed"])]
            if critical_errors: is_success = False
            
        fail_keywords = ["i failed", "could not find", "unable to", "terminated", "no task results", "fail", "hallucination", "plan..."]
        lower_res = final_res.lower()
        if any(kw in lower_res for kw in fail_keywords) or len(lower_res) < 10:
            is_success = False
            
        stop_words = {'look', 'search', 'find', 'navigate', 'click', 'check', 'website', 'page', 'following', 'today', 'items', 'for', 'the', 'and', 'with', 'from', 'that', 'this', 'these', 'those', 'list', 'show', 'give', 'tell'}
        en_keywords = [w for w in re.findall(r'[a-z]{3,}', prompt.lower()) if w not in stop_words]
        cn_keywords = re.findall(r'[\u4e00-\u9fff]{2,}', prompt)
        core_keywords = sorted(list(set(en_keywords + cn_keywords)), key=len, reverse=True)
        
        if core_keywords:
            data_heavy_terms = {'price', 'cost', 'value', 'index', 'number', 'how much', 'many', 'vix'}
            if any(term in prompt.lower() for term in data_heavy_terms) and not re.search(r'\d+', lower_res):
                is_success = False
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Validation Error: Data-heavy goal but no numerical values found in result.\n")

            if not any(kw in lower_res for kw in core_keywords):
                msg = f"Validation Error: Result does not mention core keywords {core_keywords}"
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"{msg}\n")
                is_success = False

        if not is_success:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Success Logic breakdown:\n - is_done: {is_done}\n - history.is_successful(): {history.is_successful()}\n")
                if history.has_errors(): f.write(f" - history.errors(): {history.errors()}\n")

        try:
            db.add(Output(task_id=task_id, content=final_res))
            task.status = "COMPLETED" if is_success else "FAILED"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Final Success State: {is_success}\nFinal Result: {final_res[:2000]}...\n")
            db.commit()
        except Exception as db_err:
            db.rollback()

    except Exception as e:
        task.status = "FAILED"
        db.add(Output(task_id=task_id, content=str(e)))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\nFATAL AGENT ERROR: {e}\n")
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