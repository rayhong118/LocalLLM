import calendar
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session
from backend.database.session import SessionLocal
from backend.database.models import Task as DBTask
from backend.services.task_cache import trigger_task_update

cached_schedules: List[dict] = []
cached_schedules_lock = threading.Lock()
scheduler = AsyncIOScheduler()

def calculate_next_run(
    frequency: str, 
    hour: Optional[int], 
    day_of_week: Optional[int] = None, 
    day_of_month: Optional[int] = None,
    weekday_only: Optional[bool] = False
) -> Optional[datetime]:
    if frequency == "ONCE" or hour is None:
        return None
        
    now_local = datetime.now().astimezone()
    target_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    
    if frequency == "DAILY":
        if weekday_only:
            # Mon-Fri schedule
            if target_local <= now_local:
                target_local += timedelta(days=1)
            while target_local.weekday() >= 5:  # 5=Sat, 6=Sun
                target_local += timedelta(days=1)
        else:
            if target_local <= now_local:
                target_local += timedelta(days=1)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)
        
    elif frequency == "WEEKLY":
        target_dow = day_of_week if day_of_week is not None else 0
        days_ahead = (target_dow - now_local.weekday()) % 7
        target_local += timedelta(days=days_ahead)
        if target_local <= now_local:
            target_local += timedelta(days=7)
        return target_local.astimezone(timezone.utc).replace(tzinfo=None)
        
    elif frequency == "MONTHLY":
        target_dom = day_of_month if day_of_month is not None else 1
        year = now_local.year
        month = now_local.month
        max_days = calendar.monthrange(year, month)[1]
        clamped_day = min(target_dom, max_days)
        target_local = now_local.replace(year=year, month=month, day=clamped_day, hour=hour, minute=0, second=0, microsecond=0)
        
        if target_local <= now_local:
            month += 1
            if month > 12:
                month = 1
                year += 1
            max_days = calendar.monthrange(year, month)[1]
            clamped_day = min(target_dom, max_days)
            target_local = target_local.replace(year=year, month=month, day=clamped_day)

        return target_local.astimezone(timezone.utc).replace(tzinfo=None)
        
    return None

def reload_scheduled_tasks_cache(db: Optional[Session] = None):
    """Reloads in-memory schedule definitions from DB."""
    global cached_schedules
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    try:
        tasks = db.query(DBTask).filter(
            DBTask.frequency.in_(["DAILY", "WEEKLY", "MONTHLY"])
        ).all()
        with cached_schedules_lock:
            cached_schedules = [
                {
                    "id": t.id,
                    "prompt": t.prompt,
                    "frequency": t.frequency,
                    "hour_of_day": t.hour_of_day,
                    "day_of_week": t.day_of_week,
                    "day_of_month": t.day_of_month,
                    "weekday_only": t.weekday_only,
                    "silent_if_no_result": t.silent_if_no_result,
                    "next_run_at": t.next_run_at,
                    "contexts": list(t.contexts),
                }
                for t in tasks
            ]
    except Exception as e:
        print(f"Error reloading scheduled tasks cache: {e}")
    finally:
        if should_close:
            db.close()

async def check_scheduled_tasks():
    """Periodic job checking in-memory schedules (0 DB queries when no tasks are due)."""
    from backend.services.worker import wake_worker

    global cached_schedules
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    with cached_schedules_lock:
        due_schedules = [
            s for s in cached_schedules
            if s.get("next_run_at") and s["next_run_at"] <= now
        ]
        
    if not due_schedules:
        return

    db = SessionLocal()
    try:
        for sched in due_schedules:
            task = db.query(DBTask).filter(DBTask.id == sched["id"]).first()
            if not task or not task.next_run_at or task.next_run_at > now:
                continue
                
            print(f"Triggering scheduled {task.frequency.lower()} task {task.id}: {task.prompt}")
            
            run_task = DBTask(
                prompt=task.prompt, 
                status="PENDING", 
                frequency="ONCE", 
                parent_id=task.id,
                silent_if_no_result=task.silent_if_no_result or False,
                contexts=list(task.contexts)
            )
            db.add(run_task)
            
            task.next_run_at = calculate_next_run(
                task.frequency, 
                task.hour_of_day, 
                task.day_of_week, 
                task.day_of_month,
                task.weekday_only
            )
            
        db.commit()
        reload_scheduled_tasks_cache(db)
        wake_worker()
        trigger_task_update()
            
    except Exception as e:
        print(f"Error in scheduler: {e}")
    finally:
        db.close()

def start_scheduler():
    scheduler.add_job(check_scheduled_tasks, 'interval', minutes=1)
    scheduler.start()

def stop_scheduler():
    try:
        scheduler.shutdown()
    except Exception:
        pass
