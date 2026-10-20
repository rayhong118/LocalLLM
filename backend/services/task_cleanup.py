import glob
import logging
import os
from typing import Optional
from sqlalchemy.orm import Session
from backend.database.models import Task as DBTask, Output as DBOutput

logger = logging.getLogger(__name__)

def delete_task_logs(task_id: int):
    """Delete all log files corresponding to a task ID (logs/*_task_{task_id}.log)."""
    try:
        patterns = [
            f"logs/*_task_{task_id}.log",
            f"logs/*_task_{task_id}_*.log"
        ]
        for pattern in patterns:
            for log_file in glob.glob(pattern):
                try:
                    if os.path.isfile(log_file):
                        os.remove(log_file)
                        logger.info(f"Deleted task log: {log_file}")
                except OSError as e:
                    logger.warning(f"Failed to remove log file {log_file}: {e}")
    except Exception as e:
        logger.warning(f"Error scanning logs for task {task_id}: {e}")

def cleanup_old_task_runs(db: Session, parent_id: int, max_runs: Optional[int]) -> int:
    """Retain only the latest `max_runs` child tasks for a recurring parent task.
    Deletes older child tasks from the database and removes their corresponding logs from disk.
    Active (RUNNING or PENDING) tasks are preserved.
    """
    if not max_runs or max_runs <= 0:
        return 0

    try:
        # Fetch all child runs for this parent, newest first
        child_tasks = (
            db.query(DBTask)
            .filter(DBTask.parent_id == parent_id)
            .order_by(DBTask.id.desc())
            .all()
        )

        if len(child_tasks) <= max_runs:
            return 0

        # Preserve the latest max_runs tasks; candidate older tasks for cleanup
        older_candidates = child_tasks[max_runs:]
        # Do not delete currently active tasks
        to_delete = [t for t in older_candidates if t.status not in ("RUNNING", "PENDING")]

        if not to_delete:
            return 0

        deleted_count = 0
        for child in to_delete:
            delete_task_logs(child.id)
            db.query(DBOutput).filter(DBOutput.task_id == child.id).delete()
            child.contexts.clear()
            db.delete(child)
            deleted_count += 1

        db.commit()
        logger.info(f"Cleaned up {deleted_count} old run(s) for recurring task #{parent_id} (retention: {max_runs})")
        return deleted_count
    except Exception as e:
        logger.error(f"Error during run cleanup for parent task #{parent_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return 0
