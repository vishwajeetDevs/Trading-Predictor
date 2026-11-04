import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.api.routes.analysis import router as analysis_router
from app.api.routes.derivatives import router as derivatives_router
from app.api.routes.market import router
from app.config.settings import Settings, get_settings
from app.core.logging import configure_logging
from app.database.session import describe_db_error, session_factory_from_settings
from app.services.ingestion_service import run_public_socket, runtime_status

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, session_factory=None, engine=None, start_socket: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if session_factory is None:
        engine, session_factory = session_factory_from_settings(settings)
    socket_enabled = settings.enable_websocket if start_socket is None else start_socket

    async def _study_loop(stop: asyncio.Event):
        from app.services.study_service import run_studies

        while not stop.is_set():
            def compute() -> None:
                db = session_factory()
                try:
                    run_studies(db)
                finally:
                    db.close()

            try:
                await asyncio.to_thread(compute)
            except Exception:
                log.exception("historical study failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.study_interval_seconds)
            except TimeoutError:
                continue

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = None
        study_task = None
        if socket_enabled:
            task = asyncio.create_task(run_public_socket(settings, app.state.session_factory, stop, app.state.runtime))
        if settings.enable_studies:
            study_task = asyncio.create_task(_study_loop(stop))
        derivatives_task = None
        if settings.enable_derivatives:
            derivatives_task = asyncio.create_task(_derivatives_loop(stop, settings, app.state.session_factory))
        try:
            yield
        finally:
            stop.set()
            for background in (task, study_task, derivatives_task):
                if background is not None:
                    background.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await background
            if engine is not None:
                engine.dispose()

    app = FastAPI(
        title="BTC Market Intelligence",
        version="0.1.0",
        summary="Read-only BTC futures market data from Delta Exchange India.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.engine = engine
    app.state.runtime = runtime_status
    @app.exception_handler(OperationalError)
    def database_unavailable(_request, exc: OperationalError):
        return JSONResponse(status_code=503, content={"detail": describe_db_error(exc)})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET"],
        allow_headers=["Accept"],
    )
    app.include_router(router)
    app.include_router(analysis_router)
    app.include_router(derivatives_router)
    return app


async def _derivatives_loop(stop: asyncio.Event, settings, session_factory):
    from app.services.derivatives_service import collect_once

    while not stop.is_set():
        def collect() -> None:
            db = session_factory()
            try:
                collect_once(db, settings)
            finally:
                db.close()

        try:
            await asyncio.to_thread(collect)
        except Exception:
            log.exception("derivatives collection failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.derivatives_poll_seconds)
        except TimeoutError:
            continue


def __getattr__(name: str):
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)
