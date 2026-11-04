from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://btc:btc@127.0.0.1:5432/btc_intelligence"
    delta_rest_base: str = "https://api.india.delta.exchange"
    delta_ws_url: str = "wss://public-socket.india.delta.exchange"
    lookback_days: int = 180
    request_min_interval_seconds: float = 0.25
    http_timeout_seconds: float = 20.0
    max_retries: int = 4
    user_agent: str = "btc-market-intelligence/0.1"
    enable_websocket: bool = True
    enable_studies: bool = True
    study_interval_seconds: int = 900
    enable_derivatives: bool = True
    derivatives_poll_seconds: int = 60
    orderbook_depth: int = 15
    orderbook_retention_hours: int = 48
    derivatives_retention_days: int = 90
    mark_retention_days: int = 14
    funding_near_zero: str = "0.0001"
    funding_min_sample: int = 30
    funding_lookback_days: int = 30
    mark_divergence_percent: str = "0.001"
    book_imbalance_threshold: str = "0.2"
    candle_chunk_size: int = 1500
    max_candle_page: int = 2000
    stale_after_seconds: int = 180
    ws_heartbeat_timeout_seconds: float = 35.0
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=(_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env"),
        extra="ignore",
    )


def get_settings() -> Settings:
    return Settings()
