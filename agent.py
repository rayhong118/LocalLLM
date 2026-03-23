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
        ],
    )
    
    try:
        # Fetch contexts
        contexts = db.query(Context).all()
        context_str = ""
        if contexts:
            context_str = "RELEVANT CONTEXTS AND PRIOR KNOWLEDGE:\n"
            for c in contexts:
                context_str += f"--- {c.name} ---\n{c.content}\n\n"
            context_str += "PLEASE USE THE ABOVE CONTEXTS TO INFORM YOUR ACTIONS FOR THE FOLLOWING TASK.\n\n"

        full_task = context_str + "USER TASK: " + prompt

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
                
        if not final_res:
            final_res = "No result extracted"
            
        # Save output to DB
        output = Output(task_id=task_id, content=final_res)
        db.add(output)
        task.status = "COMPLETED"
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