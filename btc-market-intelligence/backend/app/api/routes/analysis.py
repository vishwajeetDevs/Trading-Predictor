"""Read-only analysis routes. They return stored descriptions and do not place orders."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_runtime, get_settings
from app.services.study_service import build_snapshot, list_snapshots

router = APIRouter()


@router.get("/analysis/snapshot")
def analysis_snapshot(session: Session = Depends(get_db), settings=Depends(get_settings), runtime=Depends(get_runtime)):
    body = build_snapshot(session, settings, runtime)
    if body.get("status") == "no_product":
        raise HTTPException(status_code=404, detail="BTC perpetual is not in the database yet. Run the backfill command.")
    return body


@router.get("/analysis/history")
def analysis_history(limit: int = Query(default=20, ge=1, le=100), session: Session = Depends(get_db)):
    return list_snapshots(session, limit)
