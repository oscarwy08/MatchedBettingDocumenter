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
        if "reload_frequency" not in cols:
            conn.execute(text("ALTER TABLE offers ADD COLUMN reload_frequency VARCHAR(20) DEFAULT ''"))
        if "reload_stake" not in cols:
            conn.execute(text("ALTER TABLE offers ADD COLUMN reload_stake NUMERIC(12, 2) DEFAULT 0"))
        if "reload_reward" not in cols:
            conn.execute(text("ALTER TABLE offers ADD COLUMN reload_reward NUMERIC(12, 2) DEFAULT 0"))
        if "next_reload_on" not in cols:
            conn.execute(text("ALTER TABLE offers ADD COLUMN next_reload_on DATE"))
        bet_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(bets)"))}
        if "free_bet_returned" not in bet_cols:
            conn.execute(text("ALTER TABLE bets ADD COLUMN free_bet_returned INTEGER DEFAULT 0"))
        if "placed_at" not in bet_cols:
            conn.execute(text("ALTER TABLE bets ADD COLUMN placed_at DATETIME"))
            conn.execute(
                text(
                    "UPDATE bets SET placed_at = datetime(date_placed) "
                    "WHERE placed_at IS NULL AND date_placed IS NOT NULL"
                )
            )
        if "starts_at" not in bet_cols:
            conn.execute(text("ALTER TABLE bets ADD COLUMN starts_at DATETIME"))
        account_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(accounts)"))}
        if "last_checked_on" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN last_checked_on DATE"))
        if "priority" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN priority INTEGER DEFAULT 0"))
        if "restriction" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN restriction VARCHAR(40) DEFAULT ''"))
        if "notes" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN notes TEXT DEFAULT ''"))
        if "check_weekday" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN check_weekday INTEGER"))


def get_session() -> Session:
    if SessionLocal is None:
        raise RuntimeError("Database is not initialised.")
    if "db" not in g:
        g.db = SessionLocal()
    return g.db
