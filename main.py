import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
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
    outputs: List[OutputSchema] = []

    model_config = {
        "from_attributes": True
    }

from datetime import datetime, timedelta, timezone

def calculate_next_run(frequency: str, hour: Optional[int]) -> Optional[datetime]:
    now_utc = datetime.utcnow()
    
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

def run_agent_thread(task_id: int, prompt: str):
    """Run the agent in a dedicated thread with its own Proactor event loop on Windows."""
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    task = loop.create_task(agent.run_agent_task(task_id, prompt))
    active_agent_tasks[task_id] = {"loop": loop, "task": task}
    
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        print(f"Agent task {task_id} successfully cancelled.")
    except Exception as e:
        print(f"Agent task {task_id} exception: {e}")
    finally:
        active_agent_tasks.pop(task_id, None)
        loop.close()

async def check_scheduled_tasks():
    db = SessionLocal()
    try:
        # Check if ANY task is currently running
        running_task = db.query(DBTask).filter(DBTask.status == "RUNNING").first()
        if running_task:
            print(f"Skipping scheduled tasks: Task {running_task.id} is currently running.")
            return

        now = datetime.utcnow()
        # Find tasks that are due and not currently running
        tasks = db.query(DBTask).filter(
            DBTask.next_run_at <= now,
            DBTask.status != "RUNNING"
        ).all()
        
        for task in tasks:
            print(f"Triggering scheduled task {task.id}: {task.prompt}")
            
            # If it was a recurring task, schedule the next one before running
            if task.frequency == "DAILY":
                task.next_run_at = calculate_next_run(task.frequency, task.hour_of_day)
            else:
                # If it was ONCE, clear next_run_at so it doesn't run again
                task.next_run_at = None
                
            db.commit()
            
            # Start the task in a new thread to ensure Proactor loop on Windows
            threading.Thread(target=run_agent_thread, args=(task.id, task.prompt), daemon=True).start()
            
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
    
    # Trigger immediately in a new thread
    if is_once:
        threading.Thread(target=run_agent_thread, args=(db_task.id, db_task.prompt), daemon=True).start()
    
    return db_task

@app.get("/tasks", response_model=List[TaskSchema])
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(DBTask).order_by(DBTask.created_at.desc()).all()
    return tasks

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
    
    # Reset status and trigger now
    db_task.status = "PENDING"
    db_task.next_run_at = None # Since we run it now, we don't need a scheduled time
    db.commit()
    db.refresh(db_task)
    
    # Trigger immediately in a new thread
    threading.Thread(target=run_agent_thread, args=(db_task.id, db_task.prompt), daemon=True).start()
    
    return db_task

@app.post("/tasks/{task_id}/cancel", response_model=TaskSchema)
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Cancel the running asyncio Task safely from another thread
    import time
    agent_info = active_agent_tasks.get(task_id)
    if agent_info:
        agent_loop = agent_info["loop"]
        agent_task = agent_info["task"]
        agent_loop.call_soon_threadsafe(agent_task.cancel)
        time.sleep(0.5) # Give it a moment to begin aborting
    
    db_task.status = "CANCELLED"
    db.commit()
    db.refresh(db_task)
    return db_task

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)