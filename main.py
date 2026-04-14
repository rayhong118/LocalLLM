import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from fastapi.responses import StreamingResponse
import json
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import database
from database import SessionLocal, Task as DBTask, Output as DBOutput, Context as DBContext
import agent
import uvicorn
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import contextlib

# Pydantic models
class TaskCreate(BaseModel):
    prompt: str
    frequency: str = "ONCE" # ONCE, DAILY
    hour_of_day: Optional[int] = None

class ContextCreate(BaseModel):
    name: str
    content: str

class ContextSchema(BaseModel):
    id: int
    name: str
    content: str
    created_at: datetime

    model_config = {
        "from_attributes": True
    }

class OutputSchema(BaseModel):
    id: int
    content: str
    created_at: datetime

    model_config = {
        "from_attributes": True
    }

class TaskSchema(BaseModel):
    id: int
    prompt: str
    status: str
    frequency: str
    hour_of_day: Optional[int]
    next_run_at: Optional[datetime]
    started_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    parent_id: Optional[int] = None
    outputs: List[OutputSchema] = []

    model_config = {
        "from_attributes": True
    }

from datetime import datetime, timedelta, timezone

def calculate_next_run(frequency: str, hour: Optional[int]) -> Optional[datetime]:
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if frequency == "ONCE":
        return now_utc
    if frequency == "DAILY" and hour is not None:
        # Get current local time
        now_local = datetime.now().astimezone()
        
        # Determine the target hour today in local time
        target_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
        
        # If the target hour has already passed today, schedule for tomorrow
        if target_local <= now_local:
            target_local += timedelta(days=1)
            
        # Convert the calculated local target time back to naive UTC for storage/scheduler
        target_utc = target_local.astimezone(timezone.utc).replace(tzinfo=None)
        return target_utc
        
    return None

import threading

active_agent_tasks = {}

def run_agent_process(task_id: int, prompt: str):
    """Run the agent in a totally separate process to preserve logs and prevent event loop crashes."""
    import subprocess
    
    # We pass the sys.executable so it uses the same python binary (venv)
    proc = subprocess.Popen([sys.executable, "agent.py", str(task_id), prompt])
    active_agent_tasks[task_id] = {"process": proc}
    
    try:
        proc.wait() # Block until the agent finishes
    except Exception as e:
        print(f"Agent process exception: {e}")
    finally:
        active_agent_tasks.pop(task_id, None)

import time

def background_worker_loop():
    while True:
        try:
            db = SessionLocal()
            running = db.query(DBTask).filter(DBTask.status == "RUNNING").first()
            if not running:
                task = db.query(DBTask).filter(DBTask.status == "PENDING", DBTask.frequency == "ONCE").order_by(DBTask.created_at.asc()).first()
                if task:
                    task_id = task.id
                    prompt = task.prompt
                    db.close()
                    
                    print(f"\n================================================")
                    print(f" QUEUE: Starting task {task_id}: {prompt}")
                    print(f"================================================\n", flush=True)

                    # Process blocks here, giving us sequential queue behavior automatically
                    run_agent_process(task_id, prompt)
                    
                    print(f"\n================================================")
                    print(f" QUEUE: Finished task {task_id}")
                    print(f"================================================\n", flush=True)
                    continue
            db.close()
        except Exception as e:
            print(f"Error in background worker: {e}")
            
        time.sleep(2)

async def check_scheduled_tasks():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Find DAILY tasks that are due
        tasks = db.query(DBTask).filter(
            DBTask.frequency == "DAILY",
            DBTask.next_run_at <= now
        ).all()
        
        for task in tasks:
            print(f"Triggering scheduled daily task {task.id}: {task.prompt}")
            
            # Spawn ONCE task
            run_task = DBTask(prompt=task.prompt, status="PENDING", frequency="ONCE", parent_id=task.id)
            db.add(run_task)
            
            # Update next_run_at for DAILY
            task.next_run_at = calculate_next_run(task.frequency, task.hour_of_day)
            
        db.commit()
            
    except Exception as e:
        print(f"Error in scheduler: {e}")
    finally:
        db.close()

scheduler = AsyncIOScheduler()
scheduler.add_job(check_scheduled_tasks, 'interval', minutes=1)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    database.init_db()
    
    # Recover from previous crashes: mark hanging RUNNING tasks as FAILED
    db = SessionLocal()
    try:
        hanging_tasks = db.query(DBTask).filter(DBTask.status == "RUNNING").all()
        for t in hanging_tasks:
            t.status = "FAILED"
            db.add(DBOutput(task_id=t.id, content="System failure: Server shut down or crashed while the task was running."))
        if hanging_tasks:
            db.commit()
            print(f"Cleaned up {len(hanging_tasks)} hanging tasks on startup.")
    except Exception as e:
        print(f"Failed to clean up hanging tasks: {e}")
    finally:
        db.close()
        
    threading.Thread(target=background_worker_loop, daemon=True).start()
    scheduler.start()
    
    yield
    
    # Shutdown
    scheduler.shutdown()

app = FastAPI(title="LocalLLM Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def read_index():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join("frontend", "index.html"))

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/tasks", response_model=TaskSchema)
async def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    next_run = calculate_next_run(task.frequency, task.hour_of_day)
    
    # If it's a "ONCE" task, we trigger it manually in a moment,
    # so we set next_run_at to None to prevent the scheduler from picking it up.
    is_once = task.frequency == "ONCE"
    if is_once:
        next_run = None
    
    db_task = DBTask(
        prompt=task.prompt, 
        status="PENDING",
        frequency=task.frequency,
        hour_of_day=task.hour_of_day,
        next_run_at=next_run
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    # Task will be picked up by the background worker loop automatically
    
    return db_task

@app.get("/tasks", response_model=List[TaskSchema])
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(DBTask).order_by(DBTask.created_at.desc()).all()
    return tasks

async def task_event_generator():
    last_update = None
    while True:
        db = SessionLocal()
        try:
            latest_task = db.query(DBTask).order_by(DBTask.updated_at.desc()).first()
            latest_out = db.query(DBOutput).order_by(DBOutput.created_at.desc()).first()
            
            current_update = None
            if latest_task:
                current_update = latest_task.updated_at
            if latest_out and (current_update is None or latest_out.created_at > current_update):
                current_update = latest_out.created_at
                
            if current_update != last_update:
                last_update = current_update
                tasks = db.query(DBTask).order_by(DBTask.created_at.desc()).all()
                tasks_data = [TaskSchema.model_validate(t).model_dump(mode="json") for t in tasks]
                yield f"data: {json.dumps(tasks_data)}\n\n"
        except Exception as e:
            print(f"SSE Error: {e}")
        finally:
            db.close()
            
        await asyncio.sleep(1)

@app.get("/tasks/stream")
async def stream_tasks():
    return StreamingResponse(task_event_generator(), media_type="text/event-stream")

@app.get("/tasks/{task_id}", response_model=TaskSchema)
def get_task(task_id: int, db: Session = Depends(get_db)):
    t = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t

# Delete a task by ID
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
    return {"message": "Task deleted successfully"}

# Context endpoints
@app.get("/contexts", response_model=List[ContextSchema])
def list_contexts(db: Session = Depends(get_db)):
    contexts = db.query(DBContext).all()
    return contexts

@app.post("/contexts", response_model=ContextSchema)
def create_context(context: ContextCreate, db: Session = Depends(get_db)):
    db_context = DBContext(name=context.name, content=context.content)
    db.add(db_context)
    db.commit()
    db.refresh(db_context)
    return db_context

@app.delete("/contexts/{context_id}")
def delete_context(context_id: int, db: Session = Depends(get_db)):
    db_context = db.query(DBContext).filter(DBContext.id == context_id).first()
    if not db_context:
        raise HTTPException(status_code=404, detail="Context not found")
    db.delete(db_context)
    db.commit()
    return {"message": "Context deleted successfully"}

@app.put("/contexts/{context_id}", response_model=ContextSchema)
def update_context(context_id: int, context: ContextCreate, db: Session = Depends(get_db)):
    db_context = db.query(DBContext).filter(DBContext.id == context_id).first()
    if not db_context:
        raise HTTPException(status_code=404, detail="Context not found")
    db_context.name = context.name
    db_context.content = context.content
    db.commit()
    db.refresh(db_context)
    return db_context

@app.post("/tasks/{task_id}/retry", response_model=TaskSchema)
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Reset status
    db_task.status = "PENDING"
    db_task.next_run_at = None 
    db.commit()
    db.refresh(db_task)
    
    return db_task

@app.post("/tasks/{task_id}/run_now", response_model=TaskSchema)
async def run_task_now(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Spawn a ONCE task for it
    run_task = DBTask(prompt=db_task.prompt, status="PENDING", frequency="ONCE", parent_id=db_task.id)
    db.add(run_task)
    db.commit()
    
    return db_task

@app.post("/tasks/{task_id}/cancel", response_model=TaskSchema)
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Cancel the running process safely
    agent_info = active_agent_tasks.get(task_id)
    if agent_info and "process" in agent_info:
        try:
            agent_info["process"].terminate()
        except:
            pass
    
    db_task.status = "CANCELLED"
    db.commit()
    db.refresh(db_task)
    return db_task

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)