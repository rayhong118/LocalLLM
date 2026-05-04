import asyncio
import logging
import os
import re
import json
from datetime import datetime, timezone

from browser_use import Agent
from browser_use.agent.views import MessageCompactionSettings

import config
from database import SessionLocal, Task as DBTask, Output
from llm_wrapper import JsonStrippingChatOllama
from context_manager import get_relevant_context_str
from skills import controller, get_skill_descriptions
from browser_utils import cleanup_headless_chrome

from core.prompts import PLANNER_SYSTEM, CAVEMAN_PROTOCOL_TEMPLATE, STALL_WARNING, REDIRECT_MSG_TEMPLATE, PRE_FLIGHT_DATA_PROMPT
from core.evaluator import evaluate_result
from core.browser import ManagedBrowser
from core.plugin import PluginRegistry

class AgentPipeline:
    def __init__(self, task_id: int, prompt: str):
        self.task_id = task_id
        self.prompt = prompt
        self.log_path = None
        self.db = None
        self.task = None
        self.managed_browser = None
        self.llm = None
        self.plan_lines = []
        self.stall_count = 0
        self.no_thinking_count = 0
        self.last_thinking = None
        self.site_key = None
        self.context_str = ""
        self.orchestrated_plan = ""

    async def run(self):
        try:
            await self.setup()
            await self.plan()
            pre_flight_data = await self.pre_flight()
            if pre_flight_data == "PREFLIGHT_FATAL":
                raise RuntimeError("Pre-flight failed: LLM model errors prevented coupon matching. Check Ollama server status and resource availability.")
            history = await self.execute(pre_flight_data)
            await self.evaluate(history)
        except Exception as e:
            await self.handle_fatal_error(e)
        finally:
            await self.cleanup()

    async def setup(self):
        if not os.path.exists("logs"):
            os.makedirs("logs")
        
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = f"logs/{run_timestamp}_task_{self.task_id}.log"
        
        # Configure logging for this run
        self.file_handler = logging.FileHandler(self.log_path, encoding='utf-8')
        self.file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        self.file_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self.file_handler)
        
        self.log(f"=== AGENT RUN STARTED ===\nTask ID: {self.task_id}\nPrompt: {self.prompt}\nTimestamp: {run_timestamp}\n\n")

        self.db = SessionLocal()
        self.task = self.db.query(DBTask).filter(DBTask.id == self.task_id).first()
        if not self.task:
            raise ValueError(f"Task {self.task_id} not found in database")

        self.task.status = "RUNNING"
        self.task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.commit()

        cleanup_headless_chrome()

        self.llm = JsonStrippingChatOllama(
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
        self.llm.log_path = self.log_path

        self.managed_browser = ManagedBrowser()
        if await self.managed_browser.start():
            self.log("Diagnostic: Browser initialized and reset.")
        else:
            self.log("Diagnostic: Browser initialization failed or no active page.")

    async def plan(self):
        self.log("\n--- ORCHESTRATOR STEP ---")
        self.context_str, self.site_key = await get_relevant_context_str(self.db, self.prompt, self.log_path)
        
        # Fallback for site plugin detection
        if not self.site_key:
            plugins = [f.replace(".py", "") for f in os.listdir("site_skills") if f.endswith(".py")]
            for p in plugins:
                if p in self.prompt.lower():
                    self.site_key = p
                    self.log(f"Context Evaluator: Fallback detected site '{self.site_key}' from prompt keywords.")
                    break

        try:
            skill_list = get_skill_descriptions()
            planner_user = f"Context:\n{self.context_str}\n\nTask:\n{self.prompt}"
            planner_messages = [
                {"role": "system", "content": PLANNER_SYSTEM.format(skill_list=skill_list)},
                {"role": "user", "content": planner_user},
            ]
            plan_resp = await self.llm.get_client().chat(
                model=config.LLM_MODEL,
                messages=planner_messages,
                options={"temperature": 0.1, "num_ctx": 4096, "num_predict": 512},
            )
            self.orchestrated_plan = (plan_resp.message.content or "").strip()
            
            # Post-process plan
            raw_lines = self.orchestrated_plan.split('\n')
            if len(raw_lines) > 8:
                self.orchestrated_plan = '\n'.join(raw_lines[:8])
            
            self.plan_lines = [
                re.sub(r'^\d+\.\s*', '', l).strip()
                for l in self.orchestrated_plan.split('\n')
                if re.match(r'^\d+\.', l.strip())
            ]

            # Security: Filter forbidden steps
            forbidden_patterns = [r'(?:^\d+\.\s*)?extract\s*:', r'\bextract\b.*tool']
            clean_lines = []
            for l in self.orchestrated_plan.split('\n'):
                if not any(re.search(pat, l.strip(), re.IGNORECASE) for pat in forbidden_patterns):
                    clean_lines.append(l)
                else:
                    self.log(f"Orchestrator: Stripped forbidden extract step: {l.strip()}")
            
            self.orchestrated_plan = '\n'.join(clean_lines)
            
            if not self.plan_lines:
                self.log(f"Orchestrator WARNING: Plan has no numbered steps. Falling back to original prompt.")
                self.orchestrated_plan = self.prompt
            else:
                # Inject constraints
                combined = (self.context_str or "") + "\n" + (self.prompt or "")
                prohibitions = [l.strip() for l in combined.split('\n') if (l.strip().startswith("FORBIDDEN:") or l.strip().startswith("MANDATORY:")) and l.strip() not in self.orchestrated_plan]
                if prohibitions:
                    self.orchestrated_plan += "\n\n" + "\n".join(prohibitions)

            self.log(f"Orchestrated Plan:\n{self.orchestrated_plan}\n\n")
        except Exception as e:
            self.log(f"Orchestrator failed: {e}\nFalling back to original prompt.\n\n")
            self.orchestrated_plan = self.prompt

    async def pre_flight(self):
        plugin = PluginRegistry.get_plugin(self.site_key)
        if not plugin:
            return ""

        self.log(f"\n--- PRE-FLIGHT HAND-OFF: '{self.site_key}' plugin ---")
        try:
            data = await plugin.run_pre_flight(
                self.managed_browser.get_session(), 
                self.prompt, 
                self.context_str, 
                self.log_path, 
                self.llm
            )
            if data:
                self.log(f"PRE-FLIGHT SUCCESS: Data injected for '{self.site_key}' plugin.")
                return data
            else:
                self.log(f"PRE-FLIGHT FAILED: Plugin '{self.site_key}' returned no data (likely LLM errors).")
                return "PREFLIGHT_FATAL"
        except Exception as e:
            self.log(f"PRE-FLIGHT failure for '{self.site_key}': {e}")
        return ""

    async def execute(self, pre_flight_data):
        prompt_for_agent = self.orchestrated_plan
        if pre_flight_data:
            prompt_for_agent = PRE_FLIGHT_DATA_PROMPT.format(pre_flight_data=pre_flight_data[:3000])

        full_protocol = CAVEMAN_PROTOCOL_TEMPLATE.format(prompt_for_agent=prompt_for_agent)

        agent = Agent(
            task=prompt_for_agent,
            llm=self.llm,
            browser=self.managed_browser.get_session(),
            controller=controller,
            use_vision=False,
            max_steps=config.MAX_STEPS,
            max_failures=config.MAX_FAILURES,
            llm_timeout=config.LLM_TIMEOUT,
            step_timeout=600,
            extend_system_message=full_protocol,
            max_actions_per_step=1,
            include_attributes=["title", "type", "role", "placeholder"],
            max_clickable_elements_length=5000, 
            register_new_step_callback=self._on_new_step,
            message_compaction=MessageCompactionSettings(enabled=False),
            loop_detection_enabled=True,
            loop_detection_window=5,
            planning_replan_on_stall=2,
        )
        self.agent = agent

        history = None
        try:
            history = await agent.run(max_steps=config.MAX_STEPS)
        except Exception as e:
            self.log(f"\nAgent execution interrupted: {e}")
            history = getattr(agent, 'history', None)
        
        return history

    async def _on_new_step(self, agent_state, model_output, step_number):
        try:
            await self.managed_browser.prepare_step()

            if self.plan_lines and step_number <= len(self.plan_lines):
                await self.managed_browser.inject_plan(step_number, self.plan_lines[step_number - 1], len(self.plan_lines))

            # Log step details
            thinking = getattr(model_output, "thinking", "No thinking")
            if isinstance(model_output, dict): thinking = model_output.get("thinking", "No thinking")
            
            self.log(f"\n[Step {step_number}]\nTHINKING: {thinking}")
            if model_output.action:
                for act in model_output.action:
                    self.log(f"ACTION: {act.model_dump_json(exclude_none=True)}")

            # Stall Detection
            current_sig = ""
            if model_output.action:
                try:
                    current_sig = json.dumps([a.model_dump(exclude_none=True) for a in model_output.action], sort_keys=True)
                except: pass

            is_stalled = (current_sig and current_sig == self.last_thinking)
            if thinking == "No thinking": self.no_thinking_count += 1
            else: self.no_thinking_count = 0

            if self.no_thinking_count >= 3:
                is_stalled = True
                self.log(f"--- NO-THINKING STALL (count={self.no_thinking_count}) ---")

            if is_stalled: self.stall_count += 1
            else: 
                self.stall_count = 0
                await self.managed_browser.clear_stall()

            self.last_thinking = current_sig

            if self.stall_count >= 2:
                warning = STALL_WARNING.format(stall_count=self.stall_count)
                self.log(f"--- STALL INTERVENTION (count={self.stall_count}) ---\n{warning}")
                await self.managed_browser.inject_stall(warning)
                
                short_kw = self.prompt.split("items:")[-1].strip().rstrip(".") if "items:" in self.prompt else self.prompt[:50]
                redirect = REDIRECT_MSG_TEMPLATE.replace("{{stall_count}}", str(self.stall_count)).replace("{{short_keyword}}", short_kw)
                self.agent.add_new_task(redirect)

            if self.stall_count >= 8:
                self.log(f"--- HARD ABORT (stall={self.stall_count}) ---")
                self.agent.state.stopped = True

        except Exception as e:
            pass

    async def evaluate(self, history):
        final_res = history.final_result() or "No result extracted"
        if final_res == "No result extracted" and history.history:
            last_match = next((h.result[-1].extracted_content for h in reversed(history.history) if h.result), None)
            if last_match: final_res = last_match

        is_success = evaluate_result(self.prompt, final_res, history, self.log_path)
        
        self.task.status = "COMPLETED" if is_success else "FAILED"
        self.db.add(Output(task_id=self.task_id, content=final_res))
        self.log(f"Final Success State: {is_success}\nFinal Result: {final_res[:2000]}...")
        self.db.commit()

    async def handle_fatal_error(self, e):
        self.log(f"\nFATAL AGENT ERROR: {e}")
        if self.task:
            self.task.status = "FAILED"
            self.db.add(Output(task_id=self.task_id, content=str(e)))
            self.db.commit()

    async def cleanup(self):
        self.log("\n=== AGENT RUN FINISHED ===")
        if hasattr(self, 'file_handler'):
            logging.getLogger().removeHandler(self.file_handler)
        if self.managed_browser:
            await self.managed_browser.stop()
        if self.db:
            self.db.close()

    def log(self, message: str):
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{message}\n")
                f.flush()
        print(message)
