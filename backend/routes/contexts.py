import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from backend.database.session import SessionLocal
from backend.database.models import Context as DBContext
from backend.schemas import ContextCreate, ContextSchema
from backend.services import task_cache

router = APIRouter(prefix="/contexts", tags=["contexts"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("", response_model=List[ContextSchema])
def list_contexts(response: Response, db: Session = Depends(get_db)):
    cached_contexts = task_cache.get_cached_contexts_json()
    if cached_contexts is not None:
        return Response(content=cached_contexts, media_type="application/json")
        
    contexts = db.query(DBContext).all()
    data = [ContextSchema.model_validate(c).model_dump(mode="json") for c in contexts]
    json_str = json.dumps(data)
    task_cache.set_cached_contexts_json(json_str)
    return Response(content=json_str, media_type="application/json")

@router.post("", response_model=ContextSchema)
def create_context(context: ContextCreate, db: Session = Depends(get_db)):
    task_cache.invalidate_contexts_cache()
    db_context = DBContext(name=context.name, content=context.content)
    db.add(db_context)
    db.commit()
    db.refresh(db_context)
    return db_context

@router.delete("/{context_id}")
def delete_context(context_id: int, db: Session = Depends(get_db)):
    task_cache.invalidate_contexts_cache()
    db_context = db.query(DBContext).filter(DBContext.id == context_id).first()
    if not db_context:
        raise HTTPException(status_code=404, detail="Context not found")
    db.delete(db_context)
    db.commit()
    return {"message": "Context deleted successfully"}

@router.put("/{context_id}", response_model=ContextSchema)
def update_context(context_id: int, context: ContextCreate, db: Session = Depends(get_db)):
    task_cache.invalidate_contexts_cache()
    db_context = db.query(DBContext).filter(DBContext.id == context_id).first()
    if not db_context:
        raise HTTPException(status_code=404, detail="Context not found")
    db_context.name = context.name
    db_context.content = context.content
    db.commit()
    db.refresh(db_context)
    return db_context
