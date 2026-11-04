import re
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings

_SECRET_URL = re.compile(r"postgres(?:ql)?(?:\+\w+)?://\S+", re.IGNORECASE)


def describe_db_error(exc: BaseException) -> str:
    """Return a short connection error with credentials and hostnames removed."""
    raw = " ".join(str(exc).split())
    lowered = raw.lower()
    if "getaddrinfo" in lowered or "failed to resolve host" in lowered:
        return "Database host could not be resolved."
    if "timeout" in lowered or "timed out" in lowered:
        return "Database connection timed out."
    message = _SECRET_URL.sub("postgresql://***", raw)
    message = re.sub(r"host '[^']+'", "host", message)
    return message[:240]


def make_engine(database_url: str):
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            future=True,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
        pool_recycle=300,
        connect_args={"connect_timeout": 10},
    )

    @event.listens_for(engine, "do_connect")
    def _retry_connect(dialect, _record, cargs, cparams):
        last_error = None
        for attempt in range(1, 4):
            try:
                return dialect.connect(*cargs, **cparams)
            except Exception as exc:
                last_error = exc
                if "getaddrinfo" not in str(exc) or attempt == 3:
                    raise
                time.sleep(2 ** (attempt - 1))
        raise last_error

    return engine


def make_session_factory(database_url: str):
    engine = make_engine(database_url)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def session_factory_from_settings(settings: Settings):
    return make_session_factory(settings.database_url)
