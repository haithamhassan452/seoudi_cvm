"""Engine/session factory. Works on sqlite for tests and Postgres for prod."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker | None = None


def get_engine(url: str | None = None) -> Engine:
    global _ENGINE
    if _ENGINE is not None and url is None:
        return _ENGINE
    url = url or Settings().database_url
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # sqlite ignores FK constraints unless asked
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _rec):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    if url is None:
        _ENGINE = engine
    return engine


def get_session_factory(url: str | None = None) -> sessionmaker:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is not None and url is None:
        return _SESSION_FACTORY
    factory = sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)
    if url is None:
        _SESSION_FACTORY = factory
    return factory


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    factory = get_session_factory(url)
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db(url: str | None = None) -> None:
    from . import models  # noqa: F401  (register mappers)

    models.Base.metadata.create_all(get_engine(url))


def reset_engine() -> None:
    """Test helper: drop cached engine/session so a new URL takes effect."""
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSION_FACTORY = None
