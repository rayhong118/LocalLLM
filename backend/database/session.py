from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.database.models import Base

SQLALCHEMY_DATABASE_URL = "sqlite:///./tasks.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Automatic schema migration for existing SQLite database
    with engine.connect() as conn:
        for table in ["tasks"]:
            try:
                result = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                existing_cols = [row[1] for row in result]
                if "day_of_week" not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN day_of_week INTEGER"))
                if "day_of_month" not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN day_of_month INTEGER"))
            except Exception as e:
                print(f"Migration note for {table}: {e}")
        conn.commit()

