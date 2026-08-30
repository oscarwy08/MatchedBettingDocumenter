from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.charts import account_sparklines, profit_series, range_window, spark_payload, spark_points, visualiser_payload
from app.db import init_db
from app.models import Account, Bet, BetStatus, BetType, Offer
from app.seed import seed_accounts


def _session(tmp_path: Path):
    Session = init_db(tmp_path / "charts.db")
    session = Session()
    seed_accounts(session)
    return session


def _place(session, *, profit, when, bookie, exchange, offer=None, status=BetStatus.BACK_WON, bet_type=BetType.QUALIFYING):
    bet = Bet(
        offer_id=offer.id if offer else None,
        date_placed=when.date() if isinstance(when, datetime) else when,
        event="Test",
        bet_type=bet_type,
        bookie_id=bookie.id,
        exchange_id=exchange.id,
        back_stake=Decimal("10.00"),
        back_odds=Decimal("2.00"),
        lay_stake=Decimal("10.00"),
        lay_odds=Decimal("2.10"),
        commission_percent=Decimal("2"),
        cashback=Decimal("0"),
        liability=Decimal("11.00"),
        expected_profit=Decimal("1.00"),
        expected_bookie_back=Decimal("10.00"),
        expected_exchange_back=Decimal("-9.00"),
        expected_bookie_lay=Decimal("-10.00"),
        expected_exchange_lay=Decimal("9.80"),
        status=status,
        actual_profit=profit if status != BetStatus.PENDING else None,
        settled_at=when if status != BetStatus.PENDING and isinstance(when, datetime) else None,
    )
    session.add(bet)
    session.flush()
    return bet


def test_week_window_is_seven_days():
    start, end, grain = range_window("1W", date(2026, 8, 29))
    assert grain == "day"
    assert start.date() == date(2026, 8, 23)
    assert end.date() == date(2026, 8, 29)


def test_profit_series_accumulates_in_range(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _place(session, profit=Decimal("10.00"), when=datetime(2026, 8, 23, 12), bookie=bookie, exchange=exchange)
    _place(session, profit=Decimal("5.50"), when=datetime(2026, 8, 29, 9), bookie=bookie, exchange=exchange)
    _place(session, profit=Decimal("99.00"), when=datetime(2026, 8, 1, 9), bookie=bookie, exchange=exchange)
    series = profit_series(session, range_key="1W", today=date(2026, 8, 29))
    assert series["labels"][0] == "Sun"
    assert series["total"] == 15.5
    assert series["values"][0] == 10.0
    assert series["values"][-1] == 15.5
    assert series["empty"] is False


def test_pending_expected_is_separate(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _place(
        session,
        profit=None,
        when=datetime(2026, 8, 29, 10),
        bookie=bookie,
        exchange=exchange,
        status=BetStatus.PENDING,
    )
    series = profit_series(session, range_key="1D", today=date(2026, 8, 29))
    assert series["total"] == 0.0
    assert series["pending"] == 1.0


def test_visualiser_by_bookie(tmp_path: Path):
    session = _session(tmp_path)
    betfred = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    coral = session.scalars(select(Account).where(Account.name == "Coral")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _place(session, profit=Decimal("4.00"), when=datetime(2026, 8, 28, 12), bookie=betfred, exchange=exchange)
    _place(session, profit=Decimal("1.00"), when=datetime(2026, 8, 28, 13), bookie=coral, exchange=exchange)
    payload = visualiser_payload(
        session, view="by_bookie", range_key="1W", today=date(2026, 8, 29)
    )
    assert payload["kind"] == "bar"
    assert payload["labels"][0] == "Betfred"
    assert payload["values"][0] == 4.0


def test_visualiser_custom_dates(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    offer = Offer(name="Welcome", type="welcome", bookie_id=bookie.id)
    session.add(offer)
    session.flush()
    _place(
        session,
        profit=Decimal("2.25"),
        when=datetime(2026, 7, 2, 12),
        bookie=bookie,
        exchange=exchange,
        offer=offer,
    )
    payload = visualiser_payload(
        session,
        view="by_offer_type",
        start_raw="01/07/2026",
        end_raw="31/07/2026",
        today=date(2026, 8, 29),
    )
    assert payload["labels"] == ["Welcome"]
    assert payload["values"] == [2.25]
    assert payload["from"] == "01/07/2026"


def test_activity_and_cashflow(tmp_path: Path):
    from app.models import Transfer, TransferKind

    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Transfer(
            account_id=bookie.id,
            kind=TransferKind.DEPOSIT,
            amount=Decimal("20.00"),
            date=date(2026, 8, 27),
        )
    )
    _place(session, profit=Decimal("3.00"), when=datetime(2026, 8, 28, 12), bookie=bookie, exchange=exchange)
    activity = visualiser_payload(session, view="activity", range_key="1W", today=date(2026, 8, 29))
    assert activity["unit"] == "bets"
    assert activity["total"] == 1.0
    cash = visualiser_payload(session, view="cashflow", range_key="1W", today=date(2026, 8, 29))
    assert "Deposits" in cash["labels"]
    assert cash["values"][cash["labels"].index("Deposits")] == 20.0


def test_spark_points_flat_when_unchanged():
    points = spark_points([0.0, 0.0, 0.0], height=24)
    ys = [float(part.split(",")[1]) for part in points.split()]
    assert ys[0] == ys[-1] == 12.0


def test_account_sparklines_split_bookie_and_exchange(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _place(
        session,
        profit=Decimal("4.00"),
        when=datetime(2026, 8, 28, 12),
        bookie=bookie,
        exchange=exchange,
        status=BetStatus.BACK_WON,
    )
    sparks = account_sparklines(session, days=7, today=date(2026, 8, 29))
    assert bookie.id in sparks
    assert exchange.id in sparks
    assert sparks[bookie.id]["values"][-1] != sparks[exchange.id]["values"][-1]
    assert "," in sparks[bookie.id]["points"]
    assert sparks[bookie.id]["area"].endswith("Z")


def test_account_id_uses_exchange_side(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _place(
        session,
        profit=Decimal("4.00"),
        when=datetime(2026, 8, 28, 12),
        bookie=bookie,
        exchange=exchange,
        status=BetStatus.BACK_WON,
    )
    net = visualiser_payload(session, view="profit_time", range_key="1W", today=date(2026, 8, 29))
    exch = visualiser_payload(
        session, view="profit_time", account_id=exchange.id, range_key="1W", today=date(2026, 8, 29)
    )
    assert net["total"] == 4.0
    assert exch["total"] != net["total"]


def test_spark_payload_includes_area():
    art = spark_payload([0.0, 2.0, 1.0])
    assert "L" in art["area"]
    assert art["area"].endswith("Z")
    assert art["down"] is False


def test_profit_series_uses_date_placed_not_settled_at(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    last_week = date(2026, 8, 23)
    today_dt = datetime(2026, 8, 29, 16, 40)
    bet = _place(
        session,
        profit=Decimal("7.50"),
        when=last_week,
        bookie=bookie,
        exchange=exchange,
    )
    bet.placed_at = today_dt
    bet.settled_at = today_dt
    session.flush()
    series = profit_series(session, range_key="1W", today=date(2026, 8, 29))
    assert series["labels"][0] == "Sun"
    assert series["total"] == 7.5
    assert series["values"][0] == 7.5
    assert series["values"][-1] == 7.5
    activity = visualiser_payload(session, view="activity", range_key="1W", today=date(2026, 8, 29))
    assert activity["values"][0] == 1.0
    assert activity["values"][-1] == 0.0
