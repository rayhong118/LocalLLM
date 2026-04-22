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
from database import SessionLocal, Task as DBTask, Output
from llm_wrapper import JsonStrippingChatOllama
from context_manager import get_relevant_context_str
from skills import controller, get_skill_descriptions
from browser_utils import cleanup_headless_chrome
from stealth import inject_stealth, cleanup_dom, inject_stall_banner, remove_stall_banner, inject_plan_banner

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
            "num_predict": 2048,
            "num_thread": 8,
            "repeat_penalty": 1.5,
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

        # Reset stale browser state from previous run so the agent always starts fresh.
        # Without this, the agent sees Safeway already open (with stale DOM indices) and
        # skips the nav_to_url step in its plan.
        try:
            await browser.start()  # Ensure session is alive before accessing pages
            page = await browser.get_current_page()
            if page is not None:
                await page.goto("about:blank")  # No wait_until — not supported in this playwright version
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Diagnostic: Browser reset to about:blank (cleared stale session state).\n")
            else:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("Diagnostic: Browser reset skipped (no active page yet — fresh session).\n")
        except Exception as reset_err:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Diagnostic: Browser reset skipped ({reset_err}).\n")

        # DOM cleanup callback — runs before each agent step to strip junk elements
        # Also handles Stall Detection via DOM banner injection + add_new_task + hard abort
        last_url = None
        last_thinking = None
        stall_count = 0
        no_thinking_count = 0  # Track consecutive steps with no reasoning
        plan_lines = []  # Will be populated after orchestration

        async def _on_new_step(agent_state, model_output, step_number):
            nonlocal last_url, last_thinking, stall_count, no_thinking_count
            try:
                await inject_stealth(browser) # Re-patch in case of navigation/reload
                await cleanup_dom(browser)

                # Inject current plan step banner into DOM so LLM always sees it
                if plan_lines and step_number <= len(plan_lines):
                    await inject_plan_banner(browser, step_number, plan_lines[step_number - 1], len(plan_lines))


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
                    # Capture thinking from multiple possible locations
                    thinking = "No thinking"
                    if hasattr(model_output, "thinking") and model_output.thinking:
                        thinking = model_output.thinking
                    elif isinstance(model_output, dict) and model_output.get("thinking"):
                        thinking = model_output["thinking"]
                    
                    f.write(f"THINKING: {thinking}\n")
                    if model_output.action:
                        for act in model_output.action:
                            f.write(f"ACTION: {act.model_dump_json(exclude_none=True)}\n")
                    f.flush()

                # No-thinking stall detection: if the model outputs empty reasoning
                # for 3+ consecutive steps, it's acting reflexively and will loop.
                if thinking == "No thinking":
                    no_thinking_count += 1
                else:
                    no_thinking_count = 0

                if no_thinking_count >= 3 and stall_count < 2:
                    stall_count = 2  # Escalate to stall intervention threshold
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n--- NO-THINKING STALL (count={no_thinking_count}) ---\n")
                        f.write("Model has not produced any reasoning for 3+ steps. Escalating to stall intervention.\n")

                # Stall Detection: compare action signatures, not URLs.
                # URL comparison breaks on SPAs (Safeway) where URL never changes.
                current_action_sig = ""
                if model_output and model_output.action:
                    try:
                        import json as _json
                        current_action_sig = _json.dumps(
                            [a.model_dump(exclude_none=True) for a in model_output.action],
                            sort_keys=True
                        )
                    except Exception:
                        pass
                
                is_stalled = (current_action_sig and current_action_sig == last_thinking)
                
                if is_stalled:
                    stall_count += 1
                elif no_thinking_count < 3:
                    stall_count = 0
                    await remove_stall_banner(browser)
                
                last_url = None  # current_url is not defined in this scope; stall detection uses action signatures
                last_thinking = current_action_sig  # Reuse last_thinking slot for action sig
                
                if stall_count >= 2:
                    stall_warning = (
                        f"STALL DETECTED (count={stall_count}): You are repeating yourself. "
                        "You MUST use a named skill tool instead of generic actions. "
                        "AVAILABLE SKILLS: safeway_get_all_deals, safeway_filter_category, safeway_click_details, smart_click, nav_to_url. "
                        "Call the skill DIRECTLY by its tool name. Do NOT use click/input/scroll for what a skill can do."
                    )
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n--- STALL INTERVENTION (count={stall_count}) ---\n")
                        f.write(f"{stall_warning}\n\n")
                    
                    # DOM INJECTION: Agent can SEE this banner in the page state
                    await inject_stall_banner(browser, stall_warning)
                    
                    # Force add_new_task immediately at count=2 — don't wait for count=5
                    # Give exact JSON action format — model was misinterpreting Python-call syntax
                    # as javascript: URIs. Must show the exact schema field names.
                    short_keyword = prompt.split("items:")[-1].strip().rstrip(".") if "items:" in prompt else prompt[:50]
                    redirect_msg = (
                        f"STALL #{stall_count}: You MUST output ONE of these JSON actions RIGHT NOW. "
                        "Do NOT scroll. Do NOT navigate. Copy one of these exactly:\n"
                        f'  {{"safeway_get_all_deals": {{"keyword": "{short_keyword}"}}}}\n'
                        '  {"safeway_filter_category": {"category_name": "Beverages"}}\n'
                        '  {"safeway_click_details": {"index": 0}}\n'
                        "Put it in your action field. No other action is allowed."
                    )
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"--- FORCED TASK REDIRECT (stall={stall_count}) ---\n")
                    agent.add_new_task(redirect_msg)
                
                if stall_count >= 8:
                    # Hard abort: stop wasting compute
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"--- HARD ABORT (stall={stall_count}) --- Agent stuck beyond recovery.\n")
                    agent.state.stopped = True

            except Exception:
                pass  # Non-fatal

        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("Diagnostic: Retrieving DB Context...\n")
            f.flush()
            
        context_str = await get_relevant_context_str(db, prompt, log_path)
        
        # --- ORCHESTRATOR STEP ---
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n--- ORCHESTRATOR STEP ---\n")
            f.write("Diagnostic: Running Planner (reusing llm client, no GPU contention)...\n")
            f.flush()
            
        try:
            skill_list = get_skill_descriptions()
            # Reuse the existing llm's underlying Ollama client instead of creating a second
            # ChatOllama instance. A second instance causes GPU contention on single-GPU machines,
            # leading to empty planner responses. We call the raw client with a plain text prompt.
            planner_system = (
                "You are a browser automation planner using the SKILLS-FIRST pattern.\n"
                "Rewrite the user's task into a GOAL and 3-5 high-level steps.\n"
                "### AVAILABLE SKILLS ###\n"
                f"{skill_list}\n"
                "### INSTRUCTIONS ###\n"
                "Format: X. Action to take (Tool: skill_name) -> verify: [What must be visible to prove success]\n"
                "Line 1: One-sentence GOAL in English.\n"
                "Remaining lines: Numbered steps.\n"
                "CRITICAL RULES:\n"
                "- Use exact strings from Context for smart_click and smart_type.\n"
                "- For site-specific skills, use the MOST UNIQUE text/title discovered on the page as the keyword.\n"
                "- If a page has many similar buttons (like 'Details'), specify the item it belongs to in the verify condition.\n"
                "- FORBIDDEN: Do NOT use global search bars for tasks on specialized discovery pages (like Deals or Coupons).\n"
                "- FORBIDDEN: Do NOT include an 'extract' step. Use safeway_click_details to get deal info.\n"
                "Be ULTRA TERSE. No explanations. Output ONLY the GOAL line and numbered steps. No preamble."
            )
            planner_user = f"Context:\n{context_str}\n\nTask:\n{prompt}"
            planner_messages = [
                {"role": "system", "content": planner_system},
                {"role": "user", "content": planner_user},
            ]
            plan_resp = await llm.get_client().chat(
                model=config.LLM_MODEL,
                messages=planner_messages,
                options={"temperature": 0.1, "num_ctx": 4096, "num_predict": 512},
            )
            orchestrated_plan = (plan_resp.message.content or "").strip()
            
            # Truncate overly verbose plans to save context tokens
            raw_plan_lines = orchestrated_plan.split('\n')
            if len(raw_plan_lines) > 8:
                orchestrated_plan = '\n'.join(raw_plan_lines[:8])
            
            # Parse numbered step lines for the DOM banner (e.g. "1. nav_to_url...")
            import re as _re
            plan_lines[:] = [
                _re.sub(r'^\d+\.\s*', '', l).strip()
                for l in orchestrated_plan.split('\n')
                if _re.match(r'^\d+\.', l.strip())
            ]

            # GUARD: Strip any step that uses the forbidden 'extract' tool
            # Match lines like "4. extract: ..." or "extract: ..." (with or without number prefix)
            forbidden_step_patterns = [r'(?:^\d+\.\s*)?extract\s*:', r'\bextract\b.*tool']
            clean_lines = []
            removed_extract = False
            for l in orchestrated_plan.split('\n'):
                is_forbidden = any(_re.search(pat, l.strip(), _re.IGNORECASE) for pat in forbidden_step_patterns)
                if is_forbidden:
                    removed_extract = True
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"Orchestrator: Stripped forbidden extract step: {l.strip()}\n")
                else:
                    clean_lines.append(l)
            if removed_extract:
                orchestrated_plan = '\n'.join(clean_lines)
                # Re-parse plan_lines after stripping
                plan_lines[:] = [
                    _re.sub(r'^\d+\.\s*', '', l).strip()
                    for l in orchestrated_plan.split('\n')
                    if _re.match(r'^\d+\.', l.strip())
                ]

            # GUARD: Reject plan if it has no numbered steps (planner returned junk/empty output)
            if not plan_lines:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"Orchestrator WARNING: Plan has no numbered steps — output was:\n{orchestrated_plan!r}\nFalling back to original prompt.\n\n")
                orchestrated_plan = prompt
            else:
                # Auto-inject key constraints from context and prompt
                combined_instructions = (context_str or "") + "\n" + (prompt or "")
                prohibitions = []
                for line in combined_instructions.split('\n'):
                    line_stripped = line.strip()
                    if line_stripped.startswith("FORBIDDEN:") or line_stripped.startswith("MANDATORY:"):
                        if line_stripped not in orchestrated_plan:
                            prohibitions.append(line_stripped)
                
                if prohibitions:
                    orchestrated_plan += "\n\n" + "\n".join(prohibitions)

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Orchestrated Plan:\n{orchestrated_plan}\n\n")
                f.flush()
                
            prompt_for_agent = orchestrated_plan
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"Orchestrator failed: {e}\nFalling back to original prompt.\n\n")
            prompt_for_agent = prompt

        # --- PRE-FLIGHT AUTOMATION ---
        # The LLM (qwen3.5:9b in structured JSON mode) cannot call custom controller skills
        # via its JSON schema — it only knows built-in browser-use actions. So we run the
        # Safeway-specific steps here in Python BEFORE the agent starts, then inject the
        # scraped data into the agent's task so it only needs to format and report results.
        pre_flight_data = ""
        try:
            from site_skills.safeway import safeway_filter_category, safeway_get_all_deals

            # Step 1: Navigate to the deals page
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n--- PRE-FLIGHT: Navigating to Safeway deals page ---\n")
            page = await browser.get_current_page()
            await page.goto("https://www.safeway.com/loyalty/coupons-deals")
            import asyncio as _asyncio
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass  # networkidle may time out on heavy SPAs; page is still usable
            await _asyncio.sleep(5)  # Wait for SPA to load

            # Step 2: Detect category from prompt and apply filter
            category_map = {
                "ice cream": "Frozen Foods", "frozen": "Frozen Foods",
                "water": "Beverages", "drink": "Beverages", "soda": "Beverages",
                "beverage": "Beverages", "juice": "Beverages", "coffee": "Beverages",
                "meat": "Meat & Seafood", "chicken": "Meat & Seafood", "fish": "Meat & Seafood",
                "bread": "Bakery", "cake": "Bakery", "cookie": "Bakery",
                "produce": "Produce", "fruit": "Produce", "vegetable": "Produce",
                "dairy": "Dairy", "cheese": "Dairy", "yogurt": "Dairy", "milk": "Dairy",
                "snack": "Snacks", "chip": "Snacks", "cracker": "Snacks",
            }
            lower_prompt = prompt.lower()
            category = next((v for k, v in category_map.items() if k in lower_prompt), "")

            filter_result = ""
            if category:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Applying category filter '{category}'...\n")
                filter_result = await safeway_filter_category(category, browser)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT Filter Result: {filter_result}\n")
                await _asyncio.sleep(3)

            # Step 3: Extract a keyword from the prompt for deal matching
            short_keyword = prompt.split("items:")[-1].strip().rstrip(".") if "items:" in prompt else prompt[:60]

            # Step 4: Scrape all deals
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT: Scraping deals with keyword='{short_keyword}'...\n")
            scrape_result = await safeway_get_all_deals(browser, keyword=short_keyword)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT Scrape Result:\n{scrape_result[:2000]}\n")

            if scrape_result.startswith("Found deals:"):
                pre_flight_data = scrape_result
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("PRE-FLIGHT: Data collected. Agent will format results.\n\n")
            else:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT: Broad scrape (no keyword match). Retrying without filter...\n")
                # Retry without keyword filter to get any deals
                scrape_result = await safeway_get_all_deals(browser, keyword="")
                pre_flight_data = scrape_result
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"PRE-FLIGHT Broad Result:\n{scrape_result[:2000]}\n\n")

        except Exception as pf_err:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"PRE-FLIGHT FATAL: {pf_err}\n")
                f.write("Cannot proceed — browser failed to navigate to Safeway. Aborting task.\n\n")
            raise  # Propagate to outer try/except — marks task FAILED cleanly

        # If we got pre-flight data, inject it into the agent task so it can just report
        if pre_flight_data:
            prompt_for_agent = (
                f"TASK: {prompt}\n\n"
                f"SCRAPED DEAL DATA (already collected from Safeway):\n{pre_flight_data[:3000]}\n\n"
                "INSTRUCTIONS: The deal data above was already scraped from the Safeway website. "
                "Your job is to:\n"
                "1. Filter the data for items matching the task.\n"
                "2. Format a clean list of matching deals with Name, Original Price, and Deal Price.\n"
                "3. Call done(text=<your formatted list>, success=True).\n"
                "Do NOT navigate or scroll. The data is already here."
            )

        # CAVEMAN PROTOCOL (Modified for Skill-Based Execution with Schema-Enforced Reasoning)
        full_protocol = (
            "### RULES ###\n"
            "1. THINKING REQUIRED: The 'thinking' field MUST contain 3+ sentences: (a) what you see on the page now, (b) which plan step you are on, (c) why this specific action is correct and DIFFERENT from your last action.\n"
            "2. FOLLOW THE PLAN EXACTLY: Execute the GOAL steps in order. Do NOT skip steps. Your first action must match Step 1 of the plan.\n"
            "3. SKILLS-FIRST: If a site-specific skill exists (e.g. starting with site name like 'safeway_'), you MUST use it. Do NOT use generic index clicks or navigate for what a skill can handle.\n"
            "4. TOOL SAFETY: NEVER use 'evaluate()' to call skills. Use tools directly from the provided list.\n"
            "5. NO EXTRACT TOOL: Use specialized skills or observation to gather data. NEVER call 'extract'.\n"
            "6. NO NAVIGATION LOOPS: Single Page Apps (like Safeway) do NOT change URLs. If you are already at the correct domain, do NOT use 'navigate' again. Perform clicks or scrolls instead.\n"
            "7. READ BEFORE BACK: When you click 'Offer Details' or open a detail popup, you MUST read and record the product name, original price, and deal price from the page BEFORE clicking 'Back'. Do NOT click 'Back' immediately after opening details.\n"
            "### SCHEMA ###\n"
            "{\"thinking\": \"3+ sentence analysis of page state, plan step, and action rationale\", \"memory\": \"Step #\", \"action\": []}\n"
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
            max_clickable_elements_length=5000, 
            register_new_step_callback=_on_new_step,  # DOM cleanup before each step
            # Message compaction: disabled — compaction erases the GOAL and RULES
            # from context too aggressively, causing the LLM to lose plan adherence.
            # With num_ctx=32768 we have enough room to keep the full history.
            message_compaction=MessageCompactionSettings(
                enabled=False,
            ),
            # Loop detection: catch repeated actions fast
            loop_detection_enabled=True,
            loop_detection_window=5,
            # Planning: replan quickly when stuck
            planning_replan_on_stall=2,
        )
        history = None
        # Start the agent with strict step enforcement
        try:
            history = await agent.run(max_steps=config.MAX_STEPS)
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
            # Filter out transient LLM errors (formatting/timeouts/parsing) that weren't fatal
            excluded_phrases = ["closed pipe", "resourcewarning", "connection closed", "failed to parse", "invalid model output format", "timed out"]
            critical_errors = [e for e in history.errors() if not any(x in str(e).lower() for x in excluded_phrases)]
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