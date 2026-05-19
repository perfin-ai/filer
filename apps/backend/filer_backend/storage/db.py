from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from filer_backend.config import db_url


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def _setup_engine():
    engine = create_engine(
        db_url(),
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _setup_engine()
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, expire_on_commit=False
        )
    return _engine, _SessionLocal


def get_session():
    _, SessionLocal = init_engine()
    return SessionLocal()
