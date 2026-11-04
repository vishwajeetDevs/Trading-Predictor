from fastapi import Request
from sqlalchemy.orm import Session

from app.config.settings import Settings


def get_db(request: Request):
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_runtime(request: Request):
    return request.app.state.runtime


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
