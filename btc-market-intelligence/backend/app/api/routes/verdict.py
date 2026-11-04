"""Layered analysis verdict. Read-only: it describes probabilities and never places orders."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_runtime, get_settings
from app.schemas.api import VerdictRequest, VerdictResponse
from app.services.verdict_service import compute_verdict, list_runs, to_request

router = APIRouter()


def _run(body: VerdictRequest, session: Session, settings, runtime) -> dict:
    if not settings.enable_verdict:
        raise HTTPException(status_code=404, detail="The verdict engine is disabled (ENABLE_VERDICT=false).")
    result = compute_verdict(session, settings, runtime, to_request(body, settings), force_refresh=body.force_refresh)
    if result.get("status") == "no_product":
        raise HTTPException(status_code=404, detail="BTC perpetual is not in the database yet. Run the backfill command.")
    return result


@router.post("/analysis/verdict", response_model=VerdictResponse)
def analysis_verdict(body: VerdictRequest, session: Session = Depends(get_db), settings=Depends(get_settings), runtime=Depends(get_runtime)):
    return _run(body, session, settings, runtime)


@router.get("/analysis/verdict", response_model=VerdictResponse)
def analysis_verdict_get(
    focus_timeframe: str = Query(default="15m"),
    horizon: str = Query(default="4h"),
    account_size: float | None = Query(default=None, gt=0),
    risk_percent: float = Query(default=1.0, gt=0, le=10),
    force_refresh: bool = Query(default=False),
    session: Session = Depends(get_db),
    settings=Depends(get_settings),
    runtime=Depends(get_runtime),
):
    try:
        body = VerdictRequest(focus_timeframe=focus_timeframe, horizon=horizon, account_size=account_size, risk_percent=risk_percent, force_refresh=force_refresh)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _run(body, session, settings, runtime)


@router.get("/analysis/verdict/history")
def analysis_verdict_history(limit: int = Query(default=20, ge=1, le=100), session: Session = Depends(get_db)):
    return list_runs(session, limit)
