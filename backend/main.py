import os
import sys
import asyncio
import contextlib
import atexit
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from backend.database import session as database
from backend.database.session import SessionLocal
from backend.database.models import Task as DBTask, Output as DBOutput
from backend.automation.browser_utils import cleanup_browser_processes
from backend.services import task_cache
from backend.services.scheduler import start_scheduler, stop_scheduler, reload_scheduled_tasks_cache, calculate_next_run
from backend.services.worker import start_worker_thread, wake_worker
from backend.routes.tasks import router as tasks_router
from backend.routes.contexts import router as contexts_router

# Backwards-compatibility re-exports for any scripts importing from backend.main
from backend.schemas import (
    TaskCreate, TaskUpdate, TaskContextsUpdate,
    ContextCreate, ContextSchema, OutputSchema, TaskSchema
)

atexit.register(cleanup_browser_processes)

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    task_cache.init_cache_system(loop, asyncio.Condition())

    # Database schema check
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
            
        reload_scheduled_tasks_cache(db)
    except Exception as e:
        print(f"Failed to clean up hanging tasks: {e}")
    finally:
        db.close()

    # Start background execution workers & recurring scheduler
    start_worker_thread()
    start_scheduler()

    yield

    # Graceful shutdown
    stop_scheduler()
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

# Register modular routers
app.include_router(tasks_router)
app.include_router(contexts_router)

# Serve frontend build or static directory
if os.path.exists("frontend/dist"):
    app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")
    @app.get("/")
    async def read_index():
        return FileResponse(os.path.join("frontend", "dist", "index.html"))
else:
    app.mount("/static", StaticFiles(directory="frontend"), name="static")
    @app.get("/")
    async def read_index():
        return FileResponse(os.path.join("frontend", "index.html"))

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
