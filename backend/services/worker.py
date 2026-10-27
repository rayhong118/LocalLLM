import sys
import os
import time
import subprocess
import threading
from typing import Dict, Any
from backend.database.session import SessionLocal
from backend.database.models import Task as DBTask
from backend.services.task_cache import trigger_task_update

active_agent_tasks: Dict[int, Dict[str, Any]] = {}
worker_wake_event = threading.Event()

def wake_worker():
    """Wake the background worker thread immediately when a task is ready."""
    worker_wake_event.set()

def run_agent_process(task_id: int, prompt: str):
    """Run the agent in a totally separate process to preserve logs and prevent event loop crashes."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, "-m", "backend.agent", str(task_id), prompt], env=env)
    active_agent_tasks[task_id] = {"process": proc}
    
    try:
        proc.wait()  # Block until the agent finishes
    except Exception as e:
        print(f"Agent process exception: {e}")
    finally:
        active_agent_tasks.pop(task_id, None)

def cancel_active_agent(task_id: int):
    """Safely terminate an active running agent subprocess."""
    agent_info = active_agent_tasks.get(task_id)
    if agent_info and "process" in agent_info:
        try:
            agent_info["process"].terminate()
        except Exception:
            pass

def background_worker_loop():
    """Sequential queue worker that executes pending tasks one at a time."""
    while True:
        had_work = False
        try:
            db = SessionLocal()
            running = db.query(DBTask).filter(DBTask.status == "RUNNING").first()
            if not running:
                task = db.query(DBTask).filter(DBTask.status == "PENDING", DBTask.frequency == "ONCE").order_by(DBTask.created_at.asc()).first()
                if task:
                    had_work = True
                    task_id = task.id
                    prompt = task.prompt
                    db.close()
                    
                    print(f"\n================================================")
                    print(f" QUEUE: Starting task {task_id}: {prompt}")
                    print(f"================================================\n", flush=True)

                    trigger_task_update()

                    # Process blocks here, giving us sequential queue behavior automatically
                    run_agent_process(task_id, prompt)
                    
                    print(f"\n================================================")
                    print(f" QUEUE: Finished task {task_id}")
                    print(f"================================================\n", flush=True)

                    trigger_task_update()
                    continue
            db.close()
        except Exception as e:
            print(f"Error in background worker: {e}")
            
        if not had_work:
            worker_wake_event.wait(timeout=15)
            worker_wake_event.clear()
        else:
            time.sleep(1)

def start_worker_thread():
    """Start the background worker thread as a daemon."""
    worker_thread = threading.Thread(target=background_worker_loop, daemon=True)
    worker_thread.start()
    return worker_thread
