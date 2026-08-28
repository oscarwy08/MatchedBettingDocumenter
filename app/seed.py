from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Account, AccountType

EXCHANGES = [
    ("Smarkets", Decimal("2")),
    ("Betfair", Decimal("5")),
    ("Matchbook", Decimal("1")),
]

BOOKIES = [
    "Bet365",
    "Betfred",
    "Sky Bet",
    "Paddy Power",
    "William Hill",
    "Ladbrokes",
    "Coral",
    "Betway",
    "Unibet",
    "BetVictor",
    "888sport",
    "Spreadex",
    "BetMGM",
    "Grosvenor",
    "LiveScore Bet",
    "Virgin Bet",
    "BoyleSports",
    "Midnite",
    "BetUK",
    "CopyBet",
    "10bet",
    "32Red",
    "Betfair Sportsbook",
    "QuinnBet",
    "TalkSPORT BET",
    "Star Sports",
    "NetBet",
    "LeoVegas",
    "VBet",
    "Parimatch",
]


def seed_accounts(session: Session | None = None) -> None:
    close = False
    if session is None:
        if SessionLocal is None:
            raise RuntimeError("Database is not initialised.")
        session = SessionLocal()
        close = True
    existing = {name for name in session.scalars(select(Account.name))}

    for name, commission in EXCHANGES:
        if name not in existing:
            session.add(
                Account(
                    name=name,
                    type=AccountType.EXCHANGE,
                    commission_percent=commission,
                )
            )

    for name in BOOKIES:
        if name not in existing:
            session.add(
                Account(
                    name=name,
                    type=AccountType.BOOKIE,
                    commission_percent=Decimal("0"),
                )
            )

    session.commit()
    if close:
        session.close()
