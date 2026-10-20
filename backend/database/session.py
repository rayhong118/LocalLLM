import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.dialects import registry
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from backend.database.models import Base
from backend import config

logger = logging.getLogger(__name__)

# Register custom libSQL dialect to interface cleanly with Turso / libSQL
class SQLiteDialect_libsql(SQLiteDialect_pysqlite):
    driver = "libsql"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        try:
            import libsql
            return libsql
        except ImportError:
            import sqlite3
            return sqlite3

    def on_connect_url(self, url):
        # libSQL does not need or support pysqlite's create_function hooks (e.g. REGEXP, FLOOR)
        return None

registry.impls["sqlite.libsql"] = lambda: SQLiteDialect_libsql

def is_turso_enabled() -> bool:
    """Return True if Turso cloud URL or embedded replica sync URL is configured."""
    return bool(config.TURSO_DATABASE_URL or config.TURSO_SYNC_URL)

def is_embedded_replica() -> bool:
    """Return True if running in local embedded replica mode syncing to Turso."""
    return bool(config.TURSO_SYNC_URL)

def _create_database_engine():
    turso_url = config.TURSO_DATABASE_URL
    turso_token = config.TURSO_AUTH_TOKEN
    turso_sync_url = config.TURSO_SYNC_URL
    replica_path = config.TURSO_LOCAL_REPLICA_PATH

    if turso_sync_url:
        logger.info(f"Connecting to Turso via Embedded Replica (local: {replica_path}, sync: {turso_sync_url})")
        import libsql
        def get_replica_conn():
            return libsql.connect(
                database=replica_path,
                sync_url=turso_sync_url,
                auth_token=turso_token,
            )
        return create_engine(
            "sqlite+libsql://",
            creator=get_replica_conn,
            poolclass=NullPool,
        )
    elif turso_url:
        logger.info(f"Connecting to remote Turso database at {turso_url}")
        import libsql
        def get_remote_conn():
            return libsql.connect(
                database=turso_url,
                auth_token=turso_token,
            )
        return create_engine(
            "sqlite+libsql://",
            creator=get_remote_conn,
            poolclass=NullPool,
        )
    else:
        # Fallback to local SQLite tasks.db
        logger.info("Using local SQLite database: sqlite:///./tasks.db")
        return create_engine(
            "sqlite:///./tasks.db",
            connect_args={"check_same_thread": False},
        )

SQLALCHEMY_DATABASE_URL = (
    config.TURSO_DATABASE_URL
    or config.TURSO_SYNC_URL
    or "sqlite:///./tasks.db"
)

engine = _create_database_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def sync_db():
    """Trigger manual sync if operating in embedded replica mode."""
    if is_embedded_replica():
        try:
            import libsql
            conn = libsql.connect(
                database=config.TURSO_LOCAL_REPLICA_PATH,
                sync_url=config.TURSO_SYNC_URL,
                auth_token=config.TURSO_AUTH_TOKEN,
            )
            conn.sync()
            conn.close()
            logger.info("Turso embedded replica sync successful.")
        except Exception as e:
            logger.error(f"Failed to sync Turso replica: {e}")

def init_db():
    """Create all tables and perform necessary column migrations."""
    Base.metadata.create_all(bind=engine)
    # Automatic schema migration for existing SQLite/libSQL database
    with engine.connect() as conn:
        for table in ["tasks"]:
            try:
                result = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                existing_cols = [row[1] for row in result]
                if "day_of_week" not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN day_of_week INTEGER"))
                if "day_of_month" not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN day_of_month INTEGER"))
                if "weekday_only" not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN weekday_only BOOLEAN DEFAULT 0"))
                if "silent_if_no_result" not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN silent_if_no_result BOOLEAN DEFAULT 0"))
                if "max_history_runs" not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN max_history_runs INTEGER"))
            except Exception as e:
                logger.warning(f"Migration note for {table}: {e}")
        conn.commit()

    if is_embedded_replica():
        sync_db()
