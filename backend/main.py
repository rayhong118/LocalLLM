import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from fastapi.responses import StreamingResponse
import json
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.database import session as database
from backend.database.session import SessionLocal
from backend.database.models import Task as DBTask, Output as DBOutput, Context as DBContext
from backend import agent
import uvicorn
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import contextlib
import atexit
from backend.automation.browser_utils import cleanup_browser_processes

atexit.register(cleanup_browser_processes)

# Pydantic models
class TaskCreate(BaseModel):
    prompt: str
    frequency: str = "ONCE" # ONCE, DAILY, WEEKLY, MONTHLY
    hour_of_day: Optional[int] = None
    day_of_week: Optional[int] = None # 0-6 (0=Mon, 6=Sun)
    day_of_month: Optional[int] = None # 1-31
    weekday_only: Optional[bool] = False
    silent_if_no_result: Optional[bool] = False
    context_ids: Optional[List[int]] = []

class TaskUpdate(BaseModel):
    frequency: Optional[str] = None
    hour_of_day: Optional[int] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    weekday_only: Optional[bool] = None
    silent_if_no_result: Optional[bool] = None
    prompt: Optional[str] = None

class TaskContextsUpdate(BaseModel):
    context_ids: List[int]

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
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    weekday_only: Optional[bool] = False
    silent_if_no_result: Optional[bool] = False
    next_run_at: Optional[datetime]
    started_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    parent_id: Optional[int] = None
    outputs: List[OutputSchema] = []
    contexts: List[ContextSchema] = []

    model_config = {
        "from_attributes": True
    }

import calendar
from datetime import datetime, timedelta, timezone

def calculate_next_run(
    frequency: str, 
    hour: Optional[int], 
    day_of_week: Optional[int] = None, 
    day_of_month: Optional[int] = None,
    weekday_only: Optional[bool] = False
) -> Optional[datetime]:
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if frequency == "ONCE":
        return now_utc

    if hour is None:
        return None

    # Get current local time
    now_local = datetime.now().astimezone()

    if frequency == "DAILY":
        target_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target_local <= now_local:
            target_local += timedelta(days=1)
        if weekday_only:
            while target_local.weekday() >= 5: # 5=Sat, 6=Sun
                target_local += timedelta(days=1)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)

    elif frequency == "WEEKLY":
        # day_of_week: 0 = Monday, 6 = Sunday (matching now_local.weekday())
        target_dow = day_of_week if day_of_week is not None else 0
        days_ahead = (target_dow - now_local.weekday()) % 7
        target_local = (now_local + timedelta(days=days_ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if days_ahead == 0 and target_local <= now_local:
            target_local += timedelta(days=7)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)

    elif frequency == "MONTHLY":
        # day_of_month: 1-31
        target_dom = day_of_month if day_of_month is not None else 1
        year = now_local.year
        month = now_local.month
        max_days = calendar.monthrange(year, month)[1]
        clamped_day = min(target_dom, max_days)
        target_local = now_local.replace(year=year, month=month, day=clamped_day, hour=hour, minute=0, second=0, microsecond=0)
        
        if target_local <= now_local:
            # Advance to next month
            month += 1
            if month > 12:
                month = 1
                year += 1
            max_days = calendar.monthrange(year, month)[1]
            clamped_day = min(target_dom, max_days)
            target_local = target_local.replace(year=year, month=month, day=clamped_day)

        return target_local.astimezone(timezone.utc).replace(tzinfo=None)
        
    return None

import threading

active_agent_tasks = {}

def run_agent_process(task_id: int, prompt: str):
    """Run the agent in a totally separate process to preserve logs and prevent event loop crashes."""
    import subprocess
    import os
    
    # We execute it as a module backend.agent so python can resolve our absolute backend imports
    # We also pass the environment copy with PYTHONIOENCODING=utf-8 to support Windows console output.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, "-m", "backend.agent", str(task_id), prompt], env=env)
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
        # Find recurring tasks that are due
        tasks = db.query(DBTask).filter(
            DBTask.frequency.in_(["DAILY", "WEEKLY", "MONTHLY"]),
            DBTask.next_run_at <= now
        ).all()
        
        for task in tasks:
            print(f"Triggering scheduled {task.frequency.lower()} task {task.id}: {task.prompt}")
            
            # Spawn ONCE task inheriting parent's contexts and silent_if_no_result setting
            run_task = DBTask(
                prompt=task.prompt, 
                status="PENDING", 
                frequency="ONCE", 
                parent_id=task.id,
                silent_if_no_result=task.silent_if_no_result or False,
                contexts=list(task.contexts)
            )
            db.add(run_task)
            
            # Update next_run_at
            task.next_run_at = calculate_next_run(
                task.frequency, 
                task.hour_of_day, 
                task.day_of_week, 
                task.day_of_month,
                task.weekday_only
            )
            
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
    try:
        cleanup_browser_processes()
    except Exception:
        pass

app = FastAPI(title="LocalLLM Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
if os.path.exists("frontend/dist"):
    app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")
    @app.get("/")
    async def read_index():
        from fastapi.responses import FileResponse
        return FileResponse(os.path.join("frontend", "dist", "index.html"))
else:
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
    next_run = calculate_next_run(
        task.frequency, 
        task.hour_of_day, 
        task.day_of_week, 
        task.day_of_month,
        task.weekday_only
    )
    
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
        day_of_week=task.day_of_week,
        day_of_month=task.day_of_month,
        weekday_only=bool(task.weekday_only) if task.weekday_only is not None else False,
        silent_if_no_result=bool(task.silent_if_no_result) if task.silent_if_no_result is not None else False,
        next_run_at=next_run
    )
    if task.context_ids:
        db_contexts = db.query(DBContext).filter(DBContext.id.in_(task.context_ids)).all()
        db_task.contexts = db_contexts

    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    # Task will be picked up by the background worker loop automatically
    
    return db_task

@app.get("/tasks", response_model=List[TaskSchema])
def list_tasks(response: Response, page: Optional[int] = None, limit: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(DBTask).order_by(DBTask.created_at.desc())
    total_count = query.count()
    response.headers["X-Total-Count"] = str(total_count)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    
    if page is not None and limit is not None:
        offset = (page - 1) * limit
        return query.offset(offset).limit(limit).all()
    return query.all()

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

# Update a task by ID
@app.put("/tasks/{task_id}", response_model=TaskSchema)
@app.patch("/tasks/{task_id}", response_model=TaskSchema)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")

    schedule_changed = False

    if payload.frequency is not None and payload.frequency != db_task.frequency:
        db_task.frequency = payload.frequency
        schedule_changed = True
    if payload.hour_of_day is not None and payload.hour_of_day != db_task.hour_of_day:
        db_task.hour_of_day = payload.hour_of_day
        schedule_changed = True
    if payload.day_of_week is not None and payload.day_of_week != db_task.day_of_week:
        db_task.day_of_week = payload.day_of_week
        schedule_changed = True
    if payload.day_of_month is not None and payload.day_of_month != db_task.day_of_month:
        db_task.day_of_month = payload.day_of_month
        schedule_changed = True
    if payload.weekday_only is not None and payload.weekday_only != db_task.weekday_only:
        db_task.weekday_only = payload.weekday_only
        schedule_changed = True
    if payload.silent_if_no_result is not None:
        db_task.silent_if_no_result = payload.silent_if_no_result
    if payload.prompt is not None:
        trimmed = payload.prompt.strip()
        if not trimmed:
            raise HTTPException(status_code=400, detail="Task prompt cannot be empty")
        db_task.prompt = trimmed

    if db_task.frequency == "ONCE":
        db_task.next_run_at = None
    elif schedule_changed or db_task.next_run_at is None:
        db_task.next_run_at = calculate_next_run(
            db_task.frequency,
            db_task.hour_of_day,
            db_task.day_of_week,
            db_task.day_of_month,
            db_task.weekday_only
        )

    db.commit()
    db.refresh(db_task)
    return db_task

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
    
    # Spawn a ONCE task for it inheriting contexts and silent_if_no_result setting
    run_task = DBTask(
        prompt=db_task.prompt, 
        status="PENDING", 
        frequency="ONCE", 
        parent_id=db_task.id,
        silent_if_no_result=db_task.silent_if_no_result or False,
        contexts=list(db_task.contexts)
    )
    db.add(run_task)
    db.commit()
    
    return db_task

# Task context management endpoints
@app.put("/tasks/{task_id}/contexts", response_model=TaskSchema)
def set_task_contexts(task_id: int, payload: TaskContextsUpdate, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    contexts = db.query(DBContext).filter(DBContext.id.in_(payload.context_ids)).all() if payload.context_ids else []
    db_task.contexts = contexts
    db.commit()
    db.refresh(db_task)
    return db_task

@app.post("/tasks/{task_id}/contexts/{context_id}", response_model=TaskSchema)
def add_context_to_task(task_id: int, context_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    db_context = db.query(DBContext).filter(DBContext.id == context_id).first()
    if not db_context:
        raise HTTPException(status_code=404, detail="Context not found")
    if db_context not in db_task.contexts:
        db_task.contexts.append(db_context)
        db.commit()
        db.refresh(db_task)
    return db_task

@app.delete("/tasks/{task_id}/contexts/{context_id}", response_model=TaskSchema)
def remove_context_from_task(task_id: int, context_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    db_task.contexts = [c for c in db_task.contexts if c.id != context_id]
    db.commit()
    db.refresh(db_task)
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
    
    # Immediately close any browser window associated with this task
    try:
        cleanup_browser_processes()
    except Exception:
        pass
    
    db_task.status = "CANCELLED"
    db.commit()
    db.refresh(db_task)
    return db_task



if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
