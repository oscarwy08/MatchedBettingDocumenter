from __future__ import annotations

from pathlib import Path

from flask import g
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

_engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(db_path: Path) -> sessionmaker[Session]:
    global _engine, SessionLocal
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    _ensure_sqlite_columns(_engine)
    return SessionLocal


def _ensure_sqlite_columns(engine: Engine) -> None:
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(offers)"))}
        if "free_funds" not in cols:
            conn.execute(text("ALTER TABLE offers ADD COLUMN free_funds NUMERIC(12, 2) DEFAULT 0"))
        bet_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(bets)"))}
        if "free_bet_returned" not in bet_cols:
            conn.execute(text("ALTER TABLE bets ADD COLUMN free_bet_returned INTEGER DEFAULT 0"))


def get_session() -> Session:
    if SessionLocal is None:
        raise RuntimeError("Database is not initialised.")
    if "db" not in g:
        g.db = SessionLocal()
    return g.db
