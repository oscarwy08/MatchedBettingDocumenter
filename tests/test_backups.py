from decimal import Decimal

from sqlalchemy import select

from app.backups import list_backups, restore, save_current
from app.db import init_db
from app.models import Account, Bet, BetStatus, BetType
from app.seed import seed_accounts
from app.snapshot import apply_snapshot, dump_snapshot


def _bet(session, event: str):
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    smarkets = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Bet(
            event=event,
            bet_type=BetType.QUALIFYING,
            bookie_id=sky.id,
            exchange_id=smarkets.id,
            back_stake=Decimal("10.00"),
            back_odds=Decimal("2.00"),
            lay_stake=Decimal("9.62"),
            lay_odds=Decimal("2.10"),
            commission_percent=Decimal("2"),
            cashback=Decimal("0"),
            liability=Decimal("10.58"),
            expected_profit=Decimal("-0.58"),
            expected_bookie_back=Decimal("10"),
            expected_exchange_back=Decimal("-10.58"),
            expected_bookie_lay=Decimal("-10"),
            expected_exchange_lay=Decimal("9.43"),
            status=BetStatus.PENDING,
        )
    )


def test_manual_and_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    _bet(session, "First")
    session.commit()
    entry = save_current(session, why="manual")
    assert entry["counts"]["bets"] == 1
    assert list_backups()[0]["id"] == entry["id"]

    _bet(session, "Second")
    session.commit()
    assert len(list(session.scalars(select(Bet)))) == 2

    restore(session, entry["id"])
    session.commit()
    events = {bet.event for bet in session.scalars(select(Bet))}
    assert events == {"First"}
    reasons = {item["why"] for item in list_backups()}
    assert "before-restore" in reasons
    assert "manual" in reasons
    session.close()


def test_apply_snapshot_writes_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    _bet(session, "Keep")
    session.commit()
    payload = dump_snapshot(session)
    apply_snapshot(session, payload, backup_why="before-sync")
    session.commit()
    assert any(item["why"] == "before-sync" for item in list_backups())
    session.close()


def test_prune_keeps_manuals(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    session.commit()
    save_current(session, why="manual")
    for _ in range(18):
        save_current(session, why="before-sync")
    autos = [item for item in list_backups() if item["why"] == "before-sync"]
    manuals = [item for item in list_backups() if item["why"] == "manual"]
    assert len(autos) == 15
    assert len(manuals) == 1
    session.close()
