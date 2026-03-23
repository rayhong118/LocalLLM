from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import database
from database import SessionLocal, Task as DBTask, Output as DBOutput
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

class OutputSchema(BaseModel):
    id: int
    content: str
    created_at: str

    model_config = {
        "from_attributes": True
    }

class TaskSchema(BaseModel):
    id: int
    prompt: str
    status: str
    frequency: str
    hour_of_day: Optional[int]
    next_run_at: Optional[str]
    created_at: str
    updated_at: str
    outputs: List[OutputSchema] = []

    model_config = {
        "from_attributes": True
    }

def calculate_next_run(frequency: str, hour: Optional[int]) -> Optional[datetime]:
    now = datetime.utcnow()
    if frequency == "ONCE":
        return now
    if frequency == "DAILY" and hour is not None:
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return None

async def check_scheduled_tasks():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        # Find tasks that are due and not currently running
        tasks = db.query(DBTask).filter(
            DBTask.next_run_at <= now,
            DBTask.status != "RUNNING"
        ).all()
        
        for task in tasks:
            print(f"Triggering scheduled task {task.id}: {task.prompt}")
            # Reset status to PENDING so agent picks it up (though we trigger it directly here)
            # Actually, agent.run_agent_task sets it to RUNNING
            
            # If it was a recurring task, schedule the next one before running
            if task.frequency == "DAILY":
                task.next_run_at = calculate_next_run(task.frequency, task.hour_of_day)
            else:
                # If it was ONCE, clear next_run_at so it doesn't run again
                task.next_run_at = None
                
            db.commit()
            
            # Start the task
            # Using asyncio.create_task because we are in an async function
            import asyncio
            asyncio.create_task(agent.run_agent_task(task.id, task.prompt))
            
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
    
    # We don't trigger it here anymore, the scheduler will pick it up on its next tick
    # If it's "ONCE", next_run is "now", so it will run within 1 minute.
    
    return {
        "id": db_task.id,
        "prompt": db_task.prompt,
        "status": db_task.status,
        "frequency": db_task.frequency,
        "hour_of_day": db_task.hour_of_day,
        "next_run_at": db_task.next_run_at.isoformat() if db_task.next_run_at else None,
        "created_at": db_task.created_at.isoformat(),
        "updated_at": db_task.updated_at.isoformat(),
        "outputs": []
    }

@app.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(DBTask).order_by(DBTask.created_at.desc()).all()
    result = []
    for t in tasks:
        result.append({
            "id": t.id,
            "prompt": t.prompt,
            "status": t.status,
            "frequency": t.frequency,
            "hour_of_day": t.hour_of_day,
            "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
            "outputs": [{"id": o.id, "content": o.content, "created_at": o.created_at.isoformat()} for o in t.outputs]
        })
    return result

@app.get("/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    t = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "id": t.id,
        "prompt": t.prompt,
        "status": t.status,
        "frequency": t.frequency,
        "hour_of_day": t.hour_of_day,
        "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
        "outputs": [{"id": o.id, "content": o.content, "created_at": o.created_at.isoformat()} for o in t.outputs]
    }

# Delete a task by ID
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
    return {"message": "Task deleted successfully"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)