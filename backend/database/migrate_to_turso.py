"""
migrate_to_turso.py - Migrate data from local SQLite database (tasks.db) to Turso.

Usage:
    uv run python -m backend.database.migrate_to_turso
    uv run python -m backend.database.migrate_to_turso --turso-url <URL> --turso-token <TOKEN>
    uv run python -m backend.database.migrate_to_turso --dry-run
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Set

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import config
from backend.database.models import Base, Context, Output, Task, task_contexts
from backend.database.session import SQLiteDialect_libsql

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("turso_migrator")


def create_turso_engine(turso_url: str, turso_token: str):
    """Create a SQLAlchemy engine pointing directly to Turso via libsql."""
    import libsql

    def get_conn():
        return libsql.connect(database=turso_url, auth_token=turso_token)

    return create_engine(
        "sqlite+libsql://",
        creator=get_conn,
        poolclass=NullPool,
    )


def create_sqlite_source_engine(sqlite_path: str):
    """Create a SQLAlchemy engine pointing to the source SQLite database."""
    abs_path = os.path.abspath(sqlite_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Source SQLite database not found at {abs_path}")
    return create_engine(f"sqlite:///{abs_path}", connect_args={"check_same_thread": False})


def inspect_counts(engine) -> Dict[str, int]:
    """Return row counts for Context, Task, Output, and task_contexts tables."""
    counts = {}
    with engine.connect() as conn:
        for t in ["contexts", "tasks", "task_contexts", "outputs"]:
            try:
                val = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                counts[t] = val or 0
            except Exception:
                counts[t] = 0
    return counts


def run_migration(
    source_path: str = "tasks.db",
    turso_url: str = None,
    turso_token: str = None,
    dry_run: bool = False,
) -> bool:
    """Migrate all data from SQLite source_path to Turso target."""
    url = turso_url or config.TURSO_DATABASE_URL or config.TURSO_SYNC_URL
    token = turso_token or config.TURSO_AUTH_TOKEN or ""

    if not url:
        logger.error("No Turso URL provided. Set TURSO_DATABASE_URL in .env or pass --turso-url.")
        return False

    logger.info(f"Source SQLite database: {source_path}")
    logger.info(f"Target Turso database: {url}")
    if dry_run:
        logger.info("[DRY RUN MODE] No changes will be written to target.")

    # 1. Connect to source
    source_engine = create_sqlite_source_engine(source_path)
    source_counts = inspect_counts(source_engine)
    logger.info(f"Source database records: {source_counts}")

    if dry_run:
        logger.info("Dry run complete. Found records ready to migrate:")
        for t, cnt in source_counts.items():
            logger.info(f"  - {t}: {cnt} rows")
        return True

    # 2. Connect to Turso
    target_engine = create_turso_engine(url, token)

    # 3. Create schema on Turso
    logger.info("Creating tables on Turso if not present...")
    Base.metadata.create_all(bind=target_engine)

    # Disable foreign keys during migration and apply any extra columns
    with target_engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = OFF;"))
        for table in ["tasks"]:
            try:
                res = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                cols = [row[1] for row in res]
                if "day_of_week" not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN day_of_week INTEGER"))
                if "day_of_month" not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN day_of_month INTEGER"))
                if "weekday_only" not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN weekday_only BOOLEAN DEFAULT 0"))
            except Exception as e:
                logger.debug(f"Schema check note: {e}")
        conn.commit()

    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)

    with SourceSession() as src_db, TargetSession() as tgt_db:
        # A. Migrate Contexts
        contexts = src_db.query(Context).all()
        logger.info(f"Migrating {len(contexts)} Context records...")
        for c in contexts:
            existing = tgt_db.query(Context).filter(Context.id == c.id).first()
            if not existing:
                new_c = Context(
                    id=c.id,
                    name=c.name,
                    content=c.content,
                    created_at=c.created_at,
                )
                tgt_db.add(new_c)
            else:
                existing.name = c.name
                existing.content = c.content
                existing.created_at = c.created_at
        tgt_db.commit()

        # B. Migrate Tasks
        tasks = src_db.query(Task).all()
        valid_task_ids: Set[int] = {t.id for t in tasks}
        logger.info(f"Migrating {len(tasks)} Task records...")

        # Sort tasks so roots (parent_id is None) are processed first
        sorted_tasks = sorted(tasks, key=lambda x: (x.parent_id is not None, x.id))

        for t in sorted_tasks:
            # Handle orphaned parent_id gracefully if original parent was deleted
            parent_id = t.parent_id
            if parent_id and parent_id not in valid_task_ids:
                logger.warning(f"Task #{t.id} references non-existent parent_id #{parent_id}. Setting parent_id to None.")
                parent_id = None

            existing = tgt_db.query(Task).filter(Task.id == t.id).first()
            if not existing:
                new_t = Task(
                    id=t.id,
                    prompt=t.prompt,
                    status=t.status,
                    frequency=t.frequency,
                    hour_of_day=t.hour_of_day,
                    day_of_week=t.day_of_week,
                    day_of_month=t.day_of_month,
                    weekday_only=t.weekday_only,
                    next_run_at=t.next_run_at,
                    started_at=t.started_at,
                    parent_id=parent_id,
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                )
                tgt_db.add(new_t)
            else:
                existing.prompt = t.prompt
                existing.status = t.status
                existing.frequency = t.frequency
                existing.hour_of_day = t.hour_of_day
                existing.day_of_week = t.day_of_week
                existing.day_of_month = t.day_of_month
                existing.weekday_only = t.weekday_only
                existing.next_run_at = t.next_run_at
                existing.started_at = t.started_at
                existing.parent_id = parent_id
                existing.created_at = t.created_at
                existing.updated_at = t.updated_at
        tgt_db.commit()

        # C. Migrate Task-Context associations
        src_tc_rows = src_db.execute(select(task_contexts.c.task_id, task_contexts.c.context_id)).fetchall()
        logger.info(f"Migrating {len(src_tc_rows)} task_contexts associations...")
        for tid, cid in src_tc_rows:
            exists = tgt_db.execute(
                select(task_contexts.c.task_id).where(
                    task_contexts.c.task_id == tid,
                    task_contexts.c.context_id == cid,
                )
            ).fetchone()
            if not exists:
                tgt_db.execute(
                    task_contexts.insert().values(task_id=tid, context_id=cid)
                )
        tgt_db.commit()

        # D. Migrate Outputs
        outputs = src_db.query(Output).all()
        logger.info(f"Migrating {len(outputs)} Output records...")
        for o in outputs:
            existing = tgt_db.query(Output).filter(Output.id == o.id).first()
            if not existing:
                new_o = Output(
                    id=o.id,
                    task_id=o.task_id,
                    content=o.content,
                    created_at=o.created_at,
                )
                tgt_db.add(new_o)
            else:
                existing.task_id = o.task_id
                existing.content = o.content
                existing.created_at = o.created_at
        tgt_db.commit()

    # Re-enable foreign keys
    with target_engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON;"))
        conn.commit()

    # 4. Verify target row counts
    target_counts = inspect_counts(target_engine)
    logger.info("=== Migration Summary ===")
    all_matched = True
    for table in ["contexts", "tasks", "task_contexts", "outputs"]:
        src_cnt = source_counts.get(table, 0)
        tgt_cnt = target_counts.get(table, 0)
        status = "MATCH" if tgt_cnt >= src_cnt else "MISMATCH"
        if status == "MISMATCH":
            all_matched = False
        logger.info(f"  {table:15s}: Source={src_cnt:3d}  |  Target={tgt_cnt:3d}  [{status}]")

    if all_matched:
        logger.info(">>> Migration to Turso COMPLETED SUCCESSFULLY! <<<")
    else:
        logger.warning(">>> Migration completed with differences. Please review log above. <<<")

    return all_matched


def main():
    parser = argparse.ArgumentParser(description="Migrate local SQLite database to Turso")
    parser.add_argument("--source", default="tasks.db", help="Path to source SQLite database (default: tasks.db)")
    parser.add_argument("--turso-url", help="Turso database URL (e.g. libsql://your-db-org.turso.io)")
    parser.add_argument("--turso-token", help="Turso database authentication token")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report without modifying target")

    args = parser.parse_args()
    success = run_migration(
        source_path=args.source,
        turso_url=args.turso_url,
        turso_token=args.turso_token,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
