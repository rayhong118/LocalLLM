import asyncio
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.database.session import SessionLocal
from backend.database.models import Task as DBTask, Output as DBOutput, Context as DBContext
from backend.schemas import TaskCreate, TaskUpdate, TaskSchema, TaskContextsUpdate
from backend.services.task_cleanup import delete_task_logs, cleanup_old_task_runs
from backend.services.scheduler import calculate_next_run, reload_scheduled_tasks_cache
from backend.services.worker import wake_worker, cancel_active_agent
from backend.automation.browser_utils import cleanup_browser_processes
from backend.services import task_cache

router = APIRouter(prefix="/tasks", tags=["tasks"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("", response_model=TaskSchema)
async def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    next_run = calculate_next_run(
        task.frequency, 
        task.hour_of_day, 
        task.day_of_week, 
        task.day_of_month,
        task.weekday_only
    )
    
    # If it's a "ONCE" task, we trigger it manually, so next_run_at is None
    if task.frequency == "ONCE":
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
        max_history_runs=task.max_history_runs if task.frequency != "ONCE" else None,
        next_run_at=next_run
    )
    if task.context_ids:
        db_contexts = db.query(DBContext).filter(DBContext.id.in_(task.context_ids)).all()
        db_task.contexts = db_contexts

    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    if db_task.frequency != "ONCE":
        reload_scheduled_tasks_cache(db)
    else:
        wake_worker()
    task_cache.trigger_task_update()
    
    return db_task

@router.get("")
def list_tasks(request: Request, response: Response, page: Optional[int] = None, limit: Optional[int] = None, db: Session = Depends(get_db)):
    cached_etag = task_cache.get_cached_tasks_etag()
    # If no pagination, serve from in-memory cache and support ETag 304 Not Modified
    if page is None and limit is None:
        if cached_etag and request.headers.get("if-none-match") == cached_etag:
            return Response(status_code=304)
        
        cached_str = task_cache.get_cached_tasks_json_sync()
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache"
        }
        if cached_etag:
            headers["ETag"] = cached_etag
        return Response(content=cached_str, media_type="application/json", headers=headers)

    query = db.query(DBTask).order_by(DBTask.created_at.desc())
    total_count = query.count()
    response.headers["X-Total-Count"] = str(total_count)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    
    offset = (page - 1) * limit
    return query.offset(offset).limit(limit).all()

async def task_event_generator():
    client_version = -1
    
    while True:
        current_version = task_cache.get_task_data_version()
        # Check if client needs updated tasks
        if client_version != current_version:
            client_version = current_version
            try:
                tasks_data = await asyncio.to_thread(task_cache.get_cached_tasks_json_sync)
                yield f"data: {tasks_data}\n\n"
            except Exception as e:
                print(f"SSE serialization error: {e}")
                
        cond = task_cache.get_task_update_condition()
        if cond is None:
            await asyncio.sleep(2)
            continue
            
        async with cond:
            try:
                # Wait for next update or periodic heartbeat (avoids timeouts with 0 DB reads)
                await asyncio.wait_for(cond.wait(), timeout=25.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

@router.get("/stream")
async def stream_tasks():
    return StreamingResponse(task_event_generator(), media_type="text/event-stream")

@router.get("/{task_id}", response_model=TaskSchema)
def get_task(task_id: int, request: Request, response: Response, db: Session = Depends(get_db)):
    t = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
        
    etag = f'"{task_id}-{int(t.updated_at.timestamp()) if t.updated_at else 0}"'
    response.headers["ETag"] = etag
    
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
        
    if t.status in ("SUCCESS", "COMPLETED", "FAILED", "NO_RESULT", "PARTIAL_SUCCESS", "CANCELLED"):
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    else:
        response.headers["Cache-Control"] = "no-cache"
        
    return t

@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Delete child runs and their logs if this was a recurring parent task
    child_tasks = db.query(DBTask).filter(DBTask.parent_id == task_id).all()
    for child in child_tasks:
        delete_task_logs(child.id)
        db.query(DBOutput).filter(DBOutput.task_id == child.id).delete()
        child.contexts.clear()
        db.delete(child)
    db.flush()

    delete_task_logs(task.id)
    db.query(DBOutput).filter(DBOutput.task_id == task.id).delete()
    task.contexts.clear()
    db.delete(task)
    db.commit()
    
    task_cache.evict_completed_task(task_id)
    reload_scheduled_tasks_cache(db)
    task_cache.trigger_task_update()
    return {"message": "Task deleted successfully"}

@router.put("/{task_id}", response_model=TaskSchema)
@router.patch("/{task_id}", response_model=TaskSchema)
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
    if "max_history_runs" in payload.model_fields_set:
        db_task.max_history_runs = payload.max_history_runs
        if db_task.max_history_runs and db_task.max_history_runs > 0:
            cleanup_old_task_runs(db, db_task.id, db_task.max_history_runs)
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
    
    task_cache.evict_completed_task(task_id)
    reload_scheduled_tasks_cache(db)
    task_cache.trigger_task_update()
    return db_task

@router.post("/{task_id}/retry", response_model=TaskSchema)
async def retry_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    db_task.status = "PENDING"
    db_task.next_run_at = None 
    db.commit()
    db.refresh(db_task)
    
    task_cache.evict_completed_task(task_id)
    wake_worker()
    task_cache.trigger_task_update()
    return db_task

@router.post("/{task_id}/run_now", response_model=TaskSchema)
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
    
    wake_worker()
    task_cache.trigger_task_update()
    return db_task

@router.put("/{task_id}/contexts", response_model=TaskSchema)
def set_task_contexts(task_id: int, payload: TaskContextsUpdate, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    contexts = db.query(DBContext).filter(DBContext.id.in_(payload.context_ids)).all() if payload.context_ids else []
    db_task.contexts = contexts
    db.commit()
    db.refresh(db_task)
    task_cache.evict_completed_task(task_id)
    reload_scheduled_tasks_cache(db)
    task_cache.trigger_task_update()
    return db_task

@router.post("/{task_id}/contexts/{context_id}", response_model=TaskSchema)
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
        task_cache.evict_completed_task(task_id)
        reload_scheduled_tasks_cache(db)
        task_cache.trigger_task_update()
    return db_task

@router.delete("/{task_id}/contexts/{context_id}", response_model=TaskSchema)
def remove_context_from_task(task_id: int, context_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    db_task.contexts = [c for c in db_task.contexts if c.id != context_id]
    db.commit()
    db.refresh(db_task)
    task_cache.evict_completed_task(task_id)
    reload_scheduled_tasks_cache(db)
    task_cache.trigger_task_update()
    return db_task

@router.post("/{task_id}/cancel", response_model=TaskSchema)
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Cancel the running process safely
    cancel_active_agent(task_id)
    
    # Immediately close any browser window associated with this task
    try:
        cleanup_browser_processes()
    except Exception:
        pass
    
    db_task.status = "CANCELLED"
    db.commit()
    db.refresh(db_task)
    task_cache.cache_completed_task(task_id, TaskSchema.model_validate(db_task).model_dump(mode="json"))
    task_cache.trigger_task_update()
    return db_task
