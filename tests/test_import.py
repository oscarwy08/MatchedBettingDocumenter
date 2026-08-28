from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import select

from app.db import init_db
from app.importer import import_workbook
from app.models import Account, AccountType, Bet
from app.seed import seed_accounts


def test_import_generic_bet_log(tmp_path: Path):
    path = tmp_path / "old_log.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Log"
    ws.append(
        [
            "Date",
            "Bookie",
            "Event",
            "Back stake",
            "Back odds",
            "Exchange",
            "Lay stake",
            "Lay odds",
            "Net profit",
        ]
    )
    ws.append(
        ["28/08/2026", "Sky Bet", "Liverpool vs Chelsea", 10, 2.0, "Smarkets - 0%", 10, 2.0, -0.4]
    )
    wb.save(path)

    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    result = import_workbook(session, path)
    session.commit()

    assert result["counts"]["bets"] == 1
    custom = session.scalars(select(Account).where(Account.name == "Smarkets - 0%")).one()
    assert custom.type == AccountType.EXCHANGE
    bet = session.scalars(select(Bet)).one()
    assert bet.bookie.name == "Sky Bet"
    assert bet.exchange.name == "Smarkets - 0%"
    assert bet.actual_profit == Decimal("-0.40")
    session.close()


def test_zero_commission_exchange_is_listed(tmp_path: Path):
    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    session.add(
        Account(
            name="Smarkets - 0%",
            type=AccountType.EXCHANGE,
            commission_percent=Decimal("0"),
        )
    )
    session.commit()
    names = [
        account.name
        for account in session.scalars(
            select(Account)
            .where(Account.type == AccountType.EXCHANGE)
            .order_by(Account.name)
        )
    ]
    assert "Smarkets - 0%" in names
    assert "Smarkets" in names
    session.close()
