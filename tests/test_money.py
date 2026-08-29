from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.calculator import calculate
from app.db import init_db
from app.models import Account, Bet, BetStatus, BetType, Offer, Transfer, TransferKind
from app.seed import seed_accounts
from app.services import (
    account_snapshot,
    account_usage,
    reconcile_offer_deposits,
    reconcile_settlement_sides,
    settlement_amounts,
    sync_offer_deposit,
)


def _session(tmp_path: Path):
    Session = init_db(tmp_path / "test.db")
    session = Session()
    seed_accounts(session)
    return session


def _bookie(session, name="Sky Bet"):
    return session.scalars(select(Account).where(Account.name == name)).one()


def test_sync_creates_and_updates_offer_transfer(tmp_path: Path):
    session = _session(tmp_path)
    sky = _bookie(session)
    offer = Offer(name="Get £30", bookie_id=sky.id, deposit_amount=Decimal("10.00"))
    session.add(offer)
    session.flush()
    assert sync_offer_deposit(session, offer) is True
    session.flush()
    rows = list(session.scalars(select(Transfer).where(Transfer.offer_id == offer.id)))
    assert len(rows) == 1
    assert rows[0].amount == Decimal("10.00")
    assert rows[0].account_id == sky.id

    offer.deposit_amount = Decimal("20.00")
    assert sync_offer_deposit(session, offer) is True
    session.flush()
    rows = list(session.scalars(select(Transfer).where(Transfer.offer_id == offer.id)))
    assert len(rows) == 1
    assert rows[0].amount == Decimal("20.00")
    session.close()


def test_reconcile_links_existing_and_drops_duplicate(tmp_path: Path):
    session = _session(tmp_path)
    sky = _bookie(session)
    offer = Offer(name="Bet £10 Get £30", bookie_id=sky.id, deposit_amount=Decimal("10.00"))
    session.add(offer)
    session.flush()
    session.add(
        Transfer(
            account_id=sky.id,
            kind=TransferKind.DEPOSIT,
            amount=Decimal("10.00"),
            notes=f"Deposit for {offer.name}",
        )
    )
    session.add(
        Transfer(
            account_id=sky.id,
            kind=TransferKind.DEPOSIT,
            amount=Decimal("10.00"),
            notes="",
        )
    )
    session.flush()
    assert reconcile_offer_deposits(session) >= 1
    session.flush()
    rows = list(session.scalars(select(Transfer).where(Transfer.account_id == sky.id)))
    assert len(rows) == 1
    assert rows[0].offer_id == offer.id
    assert account_snapshot(session, sky)["deposited"] == Decimal("10.00")
    session.close()


def test_delete_offer_keeps_bets(tmp_path: Path):
    session = _session(tmp_path)
    sky = _bookie(session)
    smarkets = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    offer = Offer(name="Keep bets", bookie_id=sky.id)
    session.add(offer)
    session.flush()
    bet = Bet(
        offer_id=offer.id,
        event="Qualifier",
        bet_type=BetType.QUALIFYING,
        bookie_id=sky.id,
        exchange_id=smarkets.id,
        back_stake=Decimal("10"),
        back_odds=Decimal("2"),
        lay_stake=Decimal("9.62"),
        lay_odds=Decimal("2.1"),
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
    session.add(bet)
    session.flush()
    for linked in list(offer.bets):
        linked.offer_id = None
    session.delete(offer)
    session.flush()
    leftover = session.get(Bet, bet.id)
    assert leftover is not None
    assert leftover.offer_id is None
    session.close()


def test_unused_account_can_be_deleted(tmp_path: Path):
    session = _session(tmp_path)
    unused = session.scalars(select(Account).where(Account.name == "10bet")).one()
    used = _bookie(session)
    session.add(
        Transfer(account_id=used.id, kind=TransferKind.DEPOSIT, amount=Decimal("5.00"))
    )
    session.flush()
    assert account_usage(session, unused.id)["can_delete"] is True
    assert account_usage(session, used.id)["can_delete"] is False
    session.close()


def _snr_bet(session, bookie, exchange, **kwargs):
    calc = calculate(
        BetType.FREE_BET_SNR,
        back_stake=50,
        back_odds="7.0632",
        lay_odds="5.00",
        commission_percent=2,
    )
    bet = Bet(
        event="Crystal Palace vs Man City",
        bet_type=BetType.FREE_BET_SNR,
        bookie_id=bookie.id,
        exchange_id=exchange.id,
        back_stake=calc.back_stake,
        back_odds=calc.back_odds,
        lay_stake=calc.lay_stake,
        lay_odds=calc.lay_odds,
        commission_percent=calc.commission_percent,
        cashback=Decimal("0"),
        liability=calc.liability,
        expected_profit=calc.expected_profit,
        expected_bookie_back=calc.if_back_wins.bookie,
        expected_exchange_back=calc.if_back_wins.exchange,
        expected_bookie_lay=calc.if_lay_wins.bookie,
        expected_exchange_lay=calc.if_lay_wins.exchange,
        status=BetStatus.LAY_WON,
        actual_bookie_profit=calc.if_back_wins.bookie,
        actual_exchange_profit=calc.if_back_wins.exchange,
        actual_profit=Decimal("0.00"),
        **kwargs,
    )
    session.add(bet)
    session.flush()
    return calc, bet


def test_lost_free_bet_does_not_keep_bookie_returns(tmp_path: Path):
    session = _session(tmp_path)
    bookie = _bookie(session, "Betfred")
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(Transfer(account_id=bookie.id, kind=TransferKind.DEPOSIT, amount=Decimal("10.00")))
    calc, bet = _snr_bet(session, bookie, exchange)
    assert calc.if_back_wins.bookie == Decimal("303.16")
    amounts = settlement_amounts(bet)
    assert amounts["bookie"] == Decimal("0.00")
    assert amounts["net"] == Decimal("0.00")
    assert amounts["bookie"] + amounts["exchange"] == amounts["net"]

    snap = account_snapshot(session, bookie)
    assert snap["bookie_profit"] == Decimal("0.00")
    assert snap["balance"] == Decimal("10.00")
    assert snap["bookie_profit"] + snap["exchange_profit"] == snap["net_profit"]
    session.close()


def test_reconcile_rewrites_inconsistent_sides(tmp_path: Path):
    session = _session(tmp_path)
    bookie = _bookie(session, "Betfred")
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _snr_bet(session, bookie, exchange)
    assert reconcile_settlement_sides(session) == 1
    session.flush()
    bet = session.scalars(select(Bet)).one()
    assert bet.actual_bookie_profit == Decimal("0.00")
    assert bet.actual_profit == Decimal("0.00")
    assert bet.actual_bookie_profit + bet.actual_exchange_profit == bet.actual_profit
    session.close()


def test_unmatched_lost_builder_drops_back_win_payout(tmp_path: Path):
    session = _session(tmp_path)
    bookie = _bookie(session, "Betfred")
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(Transfer(account_id=bookie.id, kind=TransferKind.DEPOSIT, amount=Decimal("10.00")))
    session.add(
        Bet(
            event="Crystal Palace vs Manchester City - bet builder",
            bet_type=BetType.FREE_BET_SNR,
            bookie_id=bookie.id,
            exchange_id=exchange.id,
            back_stake=Decimal("10"),
            back_odds=Decimal("17.39"),
            lay_stake=Decimal("0"),
            lay_odds=Decimal("1.02"),
            commission_percent=Decimal("0"),
            cashback=Decimal("0"),
            liability=Decimal("0"),
            expected_profit=Decimal("81.95"),
            expected_bookie_back=Decimal("163.90"),
            expected_exchange_back=Decimal("0"),
            expected_bookie_lay=Decimal("163.90"),
            expected_exchange_lay=Decimal("0"),
            status=BetStatus.LAY_WON,
            actual_bookie_profit=Decimal("163.90"),
            actual_exchange_profit=Decimal("0"),
            actual_profit=Decimal("0"),
        )
    )
    session.flush()
    snap = account_snapshot(session, bookie)
    assert snap["bookie_profit"] == Decimal("0.00")
    assert snap["net_profit"] == Decimal("0.00")
    assert snap["balance"] == Decimal("10.00")
    session.close()


def test_consistent_sides_are_left_alone(tmp_path: Path):
    session = _session(tmp_path)
    bookie = _bookie(session, "Betfred")
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    calc = calculate(BetType.FREE_BET_SNR, 50, "5.50", "5.20", 2)
    session.add(
        Bet(
            event="Arsenal vs Spurs",
            bet_type=BetType.FREE_BET_SNR,
            bookie_id=bookie.id,
            exchange_id=exchange.id,
            back_stake=calc.back_stake,
            back_odds=calc.back_odds,
            lay_stake=calc.lay_stake,
            lay_odds=calc.lay_odds,
            commission_percent=calc.commission_percent,
            cashback=Decimal("0"),
            liability=calc.liability,
            expected_profit=calc.expected_profit,
            expected_bookie_back=calc.if_back_wins.bookie,
            expected_exchange_back=calc.if_back_wins.exchange,
            expected_bookie_lay=calc.if_lay_wins.bookie,
            expected_exchange_lay=calc.if_lay_wins.exchange,
            status=BetStatus.BACK_WON,
            actual_bookie_profit=calc.if_back_wins.bookie,
            actual_exchange_profit=calc.if_back_wins.exchange,
            actual_profit=calc.if_back_wins.total,
        )
    )
    session.flush()
    snap = account_snapshot(session, bookie)
    assert snap["bookie_profit"] == calc.if_back_wins.bookie
    assert reconcile_settlement_sides(session) == 0
    session.close()
