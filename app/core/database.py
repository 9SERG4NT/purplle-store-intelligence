"""Database engine, session factory, and the FastAPI `get_db` dependency.

Sync SQLAlchemy 2.0. Postgres in production (docker-compose), SQLite for tests.
The dependency converts connectivity failures into a clean 503 so the API
degrades gracefully instead of leaking stack traces (Part C requirement).
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


class DatabaseUnavailable(Exception):
    """Raised when the database cannot be reached; mapped to HTTP 503."""


_settings = get_settings()
_connect_args = {}
_engine_kwargs: dict = {"pool_pre_ping": True, "future": True}

if _settings.database_url.startswith("sqlite"):
    # in-memory sqlite for tests needs a shared connection across threads
    _connect_args = {"check_same_thread": False}
    from sqlalchemy.pool import StaticPool

    _engine_kwargs = {"connect_args": _connect_args, "poolclass": StaticPool, "future": True}

engine = create_engine(_settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    """Create tables. Import models first so they register on Base.metadata."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Yield a session; commit on success, rollback on error, always close.

    Connectivity errors become DatabaseUnavailable -> 503 (see main.py handler).
    """
    try:
        db = SessionLocal()
    except OperationalError as exc:  # pragma: no cover - hard to hit in tests
        raise DatabaseUnavailable(str(exc)) from exc
    try:
        yield db
        db.commit()
    except DatabaseUnavailable:
        db.rollback()
        raise
    except OperationalError as exc:
        db.rollback()
        raise DatabaseUnavailable(str(exc)) from exc
    except SQLAlchemyError:
        db.rollback()
        raise
    finally:
        db.close()
