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
from skills import controller, get_skill_descriptions
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
            "num_ctx": 32768, # Increased from default to handle long DOM + history
            "num_predict": 2048, # Increased to prevent JSON truncation
            "num_thread": 8,
            "repeat_penalty": 1.3, # Slightly increased to break loops
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
        # Also handles Stall Detection: if agent is stuck, provide a "kick" message
        last_url = None
        last_thinking = None
        stall_count = 0

        async def _on_new_step(agent_state, model_output, step_number):
            nonlocal last_url, last_thinking, stall_count
            try:
                await inject_stealth(browser) # Re-patch in case of navigation/reload
                await cleanup_dom(browser)

                # --- LIVE LOGGING ---
                with open(log_path, "a", encoding="utf-8") as f:
                    # 1. Log result of PREVIOUS step if it exists
                    if step_number > 1 and agent.history and agent.history.history:
                        last_step = agent.history.history[-1]
                        if last_step.result:
                            for res_idx, res in enumerate(last_step.result):
                                content = res.extracted_content or res.error or "Action concluded."
                                f.write(f"RESULT (Step {step_number-1}): {str(content)[:500]}\n")
                    
                    # 2. Log current step plan
                    f.write(f"\n[Step {step_number}]\n")
                    if model_output:
                        thinking = getattr(model_output, "thinking", "No thinking")
                        f.write(f"THINKING: {thinking}\n")
                        if model_output.action:
                            for act in model_output.action:
                                f.write(f"ACTION: {act.model_dump_json(exclude_none=True)}\n")
                    f.flush()

                # Stall Detection logic
                current_url = agent_state.url
                current_thinking = str(getattr(model_output, 'thinking', ''))
                
                # Check for repetition loops (identical URL + similar thinking)
                is_stalled = (current_url == last_url and (current_thinking == last_thinking or len(current_thinking) > 1000))
                
                if is_stalled:
                    stall_count += 1
                else:
                    stall_count = 0
                
                last_url = current_url
                last_thinking = current_thinking
                
                if stall_count >= 2:
                    # Provide a strong visual/textual hint in the log
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n--- STALL INTERVENTION (count={stall_count}) ---\n")
                        f.write("ADVICE: You are repeating yourself. STOP searching for the same text.\n")
                        f.write("ADVICE: If a site-specific skill fails, try a broader keyword or scroll down.\n")
                        f.write("ADVICE: If the sidebar is missing, check if it is collapsed or blocked by a modal.\n")
                        f.write("ACTION: Try a different Skill, or use scroll() to find new elements.\n\n")



            except Exception:
                pass  # Non-fatal

        
        context_str = await get_relevant_context_str(db, prompt, log_path)
        
        # --- ORCHESTRATOR STEP ---
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n--- ORCHESTRATOR STEP ---\n")
            
        try:
            skill_list = get_skill_descriptions()
            planner = ChatOllama(model=config.LLM_MODEL, temperature=0.1)
            sys_msg = SystemMessage(content=(
                "You are a browser automation planner using the SKILLS-FIRST pattern.\n"
                "Rewrite the user's task into a GOAL and 3-5 high-level steps.\n"
                "### AVAILABLE SKILLS ###\n"
                f"{skill_list}\n"
                "### INSTRUCTIONS ###\n"
                "Format: X. [Skill to use](params) -> verify: [What must be visible to prove success]\n"
                "Line 1: One-sentence GOAL in English.\n"
                "Remaining lines: Numbered steps.\n"
                "CRITICAL RULES:\n"
                "- Use exact strings from Context for smart_click and smart_type.\n"
                "- For site-specific skills, use the MOST UNIQUE text/title discovered on the page as the keyword.\n"
                "- If a page has many similar buttons (like 'Details'), specify the item it belongs to in the verify condition.\n"
                "- FORBIDDEN: Do NOT use global search bars for tasks on specialized discovery pages (like Deals or Coupons).\n"
                "Be ULTRA TERSE. No explanations."
            ))
            usr_msg = HumanMessage(content=f"Context:\n{context_str}\n\nTask:\n{prompt}")
            
            plan_res = await planner.ainvoke([sys_msg, usr_msg])
            orchestrated_plan = plan_res.content.strip()
            
            # Truncate overly verbose plans to save context tokens
            plan_lines = orchestrated_plan.split('\n')
            if len(plan_lines) > 8:
                orchestrated_plan = '\n'.join(plan_lines[:8])
            
            # Auto-inject key constraints from context and prompt that the orchestrator may have dropped
            # This enables generic task scaling: simply prefix rules with FORBIDDEN: or MANDATORY:
            combined_instructions = (context_str or "") + "\n" + (prompt or "")
            prohibitions = []
            
            for line in combined_instructions.split('\n'):
                line_stripped = line.strip()
                # Dynamically extract any constraint the user passed
                if line_stripped.startswith("FORBIDDEN:") or line_stripped.startswith("MANDATORY:"):
                    # Avoid injecting old/cluttered checkbox rules that we now handle via skills
                    if "checkbox-state" in line_stripped.lower():
                        continue
                    # Avoid adding duplicates if the Orchestrator LLM already successfully included them
                    if line_stripped not in orchestrated_plan:
                        prohibitions.append(line_stripped)

            
            if prohibitions:
                orchestrated_plan += "\n\n" + "\n".join(prohibitions)
            
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Orchestrated Plan:\n{orchestrated_plan}\n\n")
                
            prompt_for_agent = orchestrated_plan
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Orchestrator failed: {e}\nFalling back to original prompt.\n\n")
            prompt_for_agent = prompt

        # CAVEMAN PROTOCOL (Modified for Skill-Based Execution)
        full_protocol = (
            "### RULES ###\n"
            "1. JSON ONLY. NO CHAT. USE SKILLS.\n"
            "2. NO SEARCH BOX ON COUPONS. USE CATEGORIES.\n"
            "3. NO EXTRACT TOOL. READ SCREEN.\n"
            "4. ERROR? SCROLL OR CHANGE KEYWORD.\n"
            "### SCHEMA ###\n"
            "{\"thinking\": \"...\", \"memory\": \"...\", \"action\": []}\n"
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
        history = None
        try:
            history = await agent.run()
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\nAgent execution interrupted: {e}\n")
            history = getattr(agent, 'history', None)
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