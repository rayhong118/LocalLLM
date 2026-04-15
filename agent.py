import asyncio
import sys
import re
import logging
from datetime import datetime, timezone

# browser-use imports
from browser_use import Agent, BrowserSession
from browser_use.agent.views import MessageCompactionSettings

# Local imports
import config
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage
from database import SessionLocal, Task as DBTask, Output
from llm_wrapper import JsonStrippingChatOllama
from context_manager import get_relevant_context_str
from browser_utils import cleanup_headless_chrome
from stealth import inject_stealth, cleanup_dom

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
            "num_predict": 1024,
            "num_thread": 8,
            "repeat_penalty": 1.2,
            "top_k": 40,
            "top_p": 0.9
        }
    )
    llm.log_path = log_path 

    browser = BrowserSession(
        headless=False,  # CRITICAL: headed mode avoids most bot detection
        channel="chrome",  # Use system Chrome — trusted by websites over bundled Chromium
        disable_security=True,
        minimum_wait_page_load_time=config.BROWSER_WAIT_TIME,
        wait_for_network_idle_page_load_time=config.BROWSER_WAIT_TIME,
        wait_between_actions=0.8,  # Human-like delay between actions
        user_data_dir=".browser_session_web",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720},
        window_size={"width": 1280, "height": 720},
        # DOM size reduction: iframes are HUGE context consumers
        cross_origin_iframes=False,  # Skip ad iframes, tracking iframes, etc.
        max_iframes=3,              # Only process 3 most relevant iframes
        max_iframe_depth=1,         # Don't recurse into nested iframes
        args=[
            "--disable-blink-features=AutomationControlled",
            "--mute-audio",
            "--no-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
        ],
    )

    try:
        from skills import controller
        
        # Inject stealth anti-detection scripts before any navigation
        await inject_stealth(browser)
        
        # DOM cleanup callback — runs before each agent step to strip junk elements
        async def _on_new_step(agent_state, model_output, step_number):
            try:
                await cleanup_dom(browser)
            except Exception:
                pass  # Non-fatal
        
        context_str = await get_relevant_context_str(db, prompt, log_path)
        
        # --- ORCHESTRATOR STEP ---
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n--- ORCHESTRATOR STEP ---\n")
            
        try:
            planner = ChatOllama(model=config.LLM_MODEL, temperature=0.1)
            sys_msg = SystemMessage(content=(
                "You are a browser automation planner. Rewrite the user's task as:\n"
                "Line 1: One-sentence GOAL in English.\n"
                "Lines 2-6: Numbered steps (MAX 5). Each step = one browser action.\n"
                "CRITICAL RULES:\n"
                "- Copy exact addresses, zip codes, URLs, and store names from Context into your steps VERBATIM.\n"
                "- Do NOT paraphrase or omit verification details from Context.\n"
                "- If Context contains PROHIBITIONS (e.g., 'Do NOT search', 'Do NOT use search box'), "
                "you MUST include them as 'FORBIDDEN:' lines at the end of your plan.\n"
                "Be ULTRA TERSE. No explanations. No JSON."
            ))
            usr_msg = HumanMessage(content=f"Context:\n{context_str}\n\nTask:\n{prompt}")
            
            plan_res = await planner.ainvoke([sys_msg, usr_msg])
            orchestrated_plan = plan_res.content.strip()
            
            # Truncate overly verbose plans to save context tokens
            plan_lines = orchestrated_plan.split('\n')
            if len(plan_lines) > 8:
                orchestrated_plan = '\n'.join(plan_lines[:8])
            
            # Auto-inject key prohibitions from context that the orchestrator may have dropped
            lower_ctx = context_str.lower() if context_str else ""
            prohibitions = []
            if "do not search" in lower_ctx or "do not use search" in lower_ctx:
                prohibitions.append("FORBIDDEN: Do NOT use the search box. Navigate via sidebar/menu categories ONLY.")
            if "do not summarize" in lower_ctx:
                prohibitions.append("FORBIDDEN: Do NOT summarize. List every matching item individually.")
            if "offer details" in lower_ctx:
                prohibitions.append("MANDATORY: You MUST click 'Offer Details' on each coupon to verify eligibility.")
            
            if prohibitions:
                orchestrated_plan += "\n" + "\n".join(prohibitions)
            
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Orchestrated Plan:\n{orchestrated_plan}\n\n")
                
            prompt_for_agent = orchestrated_plan
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Orchestrator failed: {e}\nFalling back to original prompt.\n\n")
            prompt_for_agent = prompt

        # CAVEMAN PROTOCOL (Ultra-Terse) — context already baked into orchestrator plan
        full_protocol = (
            "### RULES ###\n"
            "1. STRICT JSON ONLY. NO CHAT. NO MARKDOWN. NO INTRODUCTIONS.\n"
            "2. Final answer MUST be: {\"action\": [{\"done\": {\"text\": \"YOUR ANSWER\"}}]}\n"
            "3. Track progress in 'memory' field.\n"
            "4. USE SKILLS: 'click_element_by_text' for buttons. Only use 'smart_search' if no FORBIDDEN rule prohibits it.\n"
            "5. NO FAKE TOOLS. You are a browser. You CANNOT write_file, edit, or run commands. Only navigate, click, type, scroll, done.\n"
            "6. NEVER REPEAT ACTIONS. If you clicked something and it did not change the page, SKIP IT and move to the next step.\n"
            "7. DO NOT use the 'extract' action. Read the text on the screen yourself and output the final answer via 'done'.\n"
            "8. If a verification step is ALREADY SATISFIED (e.g. correct store is already shown), mark it done in memory and IMMEDIATELY move to the next step.\n"
            "9. If you are stuck on a SECURITY CHECK, CAPTCHA, 'Just a moment...', or 'Verify you are human' page and cannot proceed, IMMEDIATELY fail by outputting: {\"action\": [{\"done\": {\"text\": \"Security check encountered\"}}]}\n"
            "10. OBEY ALL 'FORBIDDEN:' AND 'MANDATORY:' LINES IN THE GOAL. They are NON-NEGOTIABLE constraints.\n\n"
            "### SCHEMA ###\n"
            "{\"thinking\": \"Short logic\", \"memory\": \"Step progress\", \"action\": []}\n"
            "### EXAMPLE ###\n"
            "{\"thinking\": \"I see the coupons page. I need to click Frozen Foods.\", "
            "\"memory\": \"Step 2/5: Navigate to Frozen Foods.\", "
            "\"action\": [{\"click_element\": {\"index\": 14}}]}\n"
            f"### GOAL ###\n{prompt_for_agent}"
        )

        agent = Agent(
            task=prompt_for_agent,
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
            include_attributes=["title", "type", "role", "placeholder"],
            max_clickable_elements_length=3000,  # Reduced from 5000 to prevent context overflow
            register_new_step_callback=_on_new_step,  # DOM cleanup before each step
            # Message compaction: summarize old steps to save context
            message_compaction=MessageCompactionSettings(
                enabled=True,
                compact_every_n_steps=2,  # Compact more often to keep context lean
                keep_last_items=2,
                summary_max_chars=1000,  # Shorter summaries
            ),
            # Loop detection: catch repeated actions fast
            loop_detection_enabled=True,
            loop_detection_window=5,
            # Planning: replan quickly when stuck
            planning_replan_on_stall=2,
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
            critical_errors = [e for e in history.errors() if not any(x in str(e).lower() for x in ["closed pipe", "resourcewarning", "connection closed", "failed to parse"])]
            if critical_errors: is_success = False
            
        fail_keywords = ["i failed", "could not find", "unable to", "terminated", "no task results", "fail", "hallucination", "plan...",
                         "captcha", "bot detection", "access denied", "security check", "verify you are human", "blocked"]
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