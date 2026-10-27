import asyncio
import json
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from backend.database.session import SessionLocal
from backend.database.models import Task as DBTask
from backend.schemas import TaskSchema

main_loop: Optional[asyncio.AbstractEventLoop] = None
task_update_condition: Optional[asyncio.Condition] = None
task_data_version: int = 0
_cached_tasks_json: Optional[str] = None
_cached_tasks_etag: str = ""
_completed_tasks_cache: Dict[int, dict] = {}
_cached_contexts_json: Optional[str] = None

def init_cache_system(loop: asyncio.AbstractEventLoop, condition: asyncio.Condition):
    """Initialize event loop and condition variable for async SSE notifications."""
    global main_loop, task_update_condition
    main_loop = loop
    task_update_condition = condition

async def notify_task_changed():
    """Asynchronously bump version, invalidate tasks cache, and notify all SSE subscribers."""
    global task_data_version, _cached_tasks_json
    task_data_version += 1
    _cached_tasks_json = None
    if task_update_condition:
        async with task_update_condition:
            task_update_condition.notify_all()

def trigger_task_update():
    """Thread-safe trigger to notify event loop that task state has changed."""
    global main_loop
    if main_loop and main_loop.is_running():
        try:
            main_loop.call_soon_threadsafe(asyncio.create_task, notify_task_changed())
        except Exception as e:
            print(f"Error triggering task update: {e}")

def get_task_data_version() -> int:
    return task_data_version

def get_task_update_condition() -> Optional[asyncio.Condition]:
    return task_update_condition

def get_cached_tasks_etag() -> str:
    return _cached_tasks_etag

def get_cached_tasks_json_sync() -> str:
    """Synchronously retrieve cached tasks JSON or build it from database."""
    global _cached_tasks_json, _cached_tasks_etag, _completed_tasks_cache
    if _cached_tasks_json is not None:
        return _cached_tasks_json

    db = SessionLocal()
    try:
        tasks = db.query(DBTask).order_by(DBTask.created_at.desc()).all()
        tasks_data = []
        terminal_statuses = {"SUCCESS", "COMPLETED", "FAILED", "NO_RESULT", "PARTIAL_SUCCESS", "CANCELLED"}

        for t in tasks:
            if t.status in terminal_statuses and t.id in _completed_tasks_cache:
                tasks_data.append(_completed_tasks_cache[t.id])
            else:
                dumped = TaskSchema.model_validate(t).model_dump(mode="json")
                if t.status in terminal_statuses:
                    _completed_tasks_cache[t.id] = dumped
                tasks_data.append(dumped)

        payload = json.dumps(tasks_data)
        _cached_tasks_json = payload
        _cached_tasks_etag = f'"{abs(hash(payload))}"'
        return payload
    finally:
        db.close()

def cache_completed_task(task_id: int, task_data: dict):
    _completed_tasks_cache[task_id] = task_data

def evict_completed_task(task_id: int):
    _completed_tasks_cache.pop(task_id, None)

def get_cached_contexts_json() -> Optional[str]:
    return _cached_contexts_json

def set_cached_contexts_json(json_str: str):
    global _cached_contexts_json
    _cached_contexts_json = json_str

def invalidate_contexts_cache():
    global _cached_contexts_json
    _cached_contexts_json = None
