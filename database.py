from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timezone

SQLALCHEMY_DATABASE_URL = "sqlite:///./tasks.db"

Base = declarative_base()

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    prompt = Column(Text, nullable=False)
    status = Column(String, default="PENDING") # PENDING, RUNNING, COMPLETED, FAILED
    
    # Scheduling fields
    frequency = Column(String, default="ONCE") # ONCE, DAILY
    hour_of_day = Column(Integer, nullable=True) # 0-23
    next_run_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    
    parent_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
 
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    outputs = relationship("Output", back_populates="task", cascade="all, delete-orphan")

class Output(Base):
    __tablename__ = "outputs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"))
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    task = relationship("Task", back_populates="outputs")

class Context(Base):
    __tablename__ = "contexts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
