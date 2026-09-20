from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.auth.config import database_url


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def get_engine():
    url = database_url()
    kwargs = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update({"pool_recycle": 1800})
    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


@contextmanager
def db_session():
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_auth_tables() -> None:
    # Import models before create_all so metadata contains all tables.
    from app.auth import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())


def check_auth_db_connectivity() -> dict:
    try:
        from sqlalchemy import text
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": f"Authentication database unavailable: {exc.__class__.__name__}"}
