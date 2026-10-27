from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class TaskCreate(BaseModel):
    prompt: str
    frequency: str = "ONCE"  # ONCE, DAILY, WEEKLY, MONTHLY
    hour_of_day: Optional[int] = None
    day_of_week: Optional[int] = None  # 0-6 (0=Mon, 6=Sun)
    day_of_month: Optional[int] = None  # 1-31
    weekday_only: Optional[bool] = False
    silent_if_no_result: Optional[bool] = False
    max_history_runs: Optional[int] = None
    context_ids: Optional[List[int]] = []

class TaskUpdate(BaseModel):
    frequency: Optional[str] = None
    hour_of_day: Optional[int] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    weekday_only: Optional[bool] = None
    silent_if_no_result: Optional[bool] = None
    max_history_runs: Optional[int] = None
    prompt: Optional[str] = None

class TaskContextsUpdate(BaseModel):
    context_ids: List[int]

class ContextCreate(BaseModel):
    name: str
    content: str

class ContextSchema(BaseModel):
    id: int
    name: str
    content: str
    created_at: datetime

    model_config = {
        "from_attributes": True
    }

class OutputSchema(BaseModel):
    id: int
    content: str
    created_at: datetime

    model_config = {
        "from_attributes": True
    }

class TaskSchema(BaseModel):
    id: int
    prompt: str
    status: str
    frequency: str
    hour_of_day: Optional[int]
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    weekday_only: Optional[bool] = False
    silent_if_no_result: Optional[bool] = False
    max_history_runs: Optional[int] = None
    next_run_at: Optional[datetime]
    started_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    parent_id: Optional[int] = None
    outputs: List[OutputSchema] = []
    contexts: List[ContextSchema] = []

    model_config = {
        "from_attributes": True
    }
