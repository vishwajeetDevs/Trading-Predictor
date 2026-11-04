"""Read-only derivatives routes. They read stored observations and do not call the exchange."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.services.derivatives_service import (
    funding_view,
    mark_price_view,
    open_interest_view,
    orderbook_view,
    snapshot_view,
)

router = APIRouter(prefix="/derivatives")


def _read(builder, session, settings):
    try:
        return builder(session, settings)
    except LookupError:
        raise HTTPException(status_code=404, detail="BTC perpetual is not in the database yet. Run the backfill command.") from None


@router.get("/snapshot")
def snapshot(session: Session = Depends(get_db), settings=Depends(get_settings)):
    return _read(snapshot_view, session, settings)


@router.get("/funding")
def funding(session: Session = Depends(get_db), settings=Depends(get_settings)):
    return _read(funding_view, session, settings)


@router.get("/open-interest")
def open_interest(session: Session = Depends(get_db), settings=Depends(get_settings)):
    return _read(open_interest_view, session, settings)


@router.get("/orderbook")
def orderbook(session: Session = Depends(get_db), settings=Depends(get_settings)):
    return _read(orderbook_view, session, settings)


@router.get("/mark-price")
def mark_price(session: Session = Depends(get_db), settings=Depends(get_settings)):
    return _read(mark_price_view, session, settings)
