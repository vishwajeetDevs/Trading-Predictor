import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings
from app.database.models import Base

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def btc_product() -> dict:
    body = load_json("product_btcusd.json")
    return body["result"] if isinstance(body, dict) and "result" in body else body


def make_settings(**overrides) -> Settings:
    values = dict(
        database_url="sqlite://",
        lookback_days=1,
        request_min_interval_seconds=0,
        http_timeout_seconds=5,
        max_retries=3,
        user_agent="btc-market-intelligence/test",
        enable_websocket=False,
        enable_studies=False,
        enable_derivatives=False,
        candle_chunk_size=10,
        max_candle_page=2000,
        stale_after_seconds=180,
        ws_heartbeat_timeout_seconds=35,
        log_level="WARNING",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def engine():
    db = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(db)
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
