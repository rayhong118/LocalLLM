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

app = FastAPI(title="LocalLLM Agent API")

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

# Initialize DB on startup
database.init_db()

# Pydantic models
class TaskCreate(BaseModel):
    prompt: str

class OutputSchema(BaseModel):
    id: int
    content: str
    created_at: str

    class Config:
        orm_mode = True

class TaskSchema(BaseModel):
    id: int
    prompt: str
    status: str
    created_at: str
    updated_at: str
    outputs: List[OutputSchema] = []

    class Config:
        orm_mode = True

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/tasks", response_model=TaskSchema)
async def create_task(task: TaskCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    db_task = DBTask(prompt=task.prompt, status="PENDING")
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    # Run agent in background
    background_tasks.add_task(agent.run_agent_task, db_task.id, db_task.prompt)
    
    # Manually convert datetime to string for schema (pydantic handles this usually but let's be safe)
    return {
        "id": db_task.id,
        "prompt": db_task.prompt,
        "status": db_task.status,
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
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
        "outputs": [{"id": o.id, "content": o.content, "created_at": o.created_at.isoformat()} for o in t.outputs]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
