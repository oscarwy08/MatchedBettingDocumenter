from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session as web_session,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.calculator import CalcBetType, calculate
from app.charts import (
    RANGES,
    VIEWS,
    account_sparklines,
    accounts_profit_bars,
    apply_sparklines,
    profit_series,
    visualiser_payload,
)
from app.dates import combine_date, format_uk, local_now, parse_uk
from app.db import get_session
from app.excel import preview_workbook, sync_workbook
from app.importer import import_workbook
from app.models import (
    Account,
    AccountType,
    Bet,
    BetStatus,
    BetType,
    Offer,
    OfferType,
    Transfer,
    TransferKind,
)
from app.services import (
    account_snapshot,
    account_usage,
    dashboard_stats,
    offer_snapshot,
    reconcile_offer_deposits,
    reconcile_settlement_sides,
    suggested_settlement,
    sync_offer_deposit,
)
from app.snapshot import apply_snapshot, dump_snapshot, would_shrink
from app.sync import (
    adopt_pair_secret,
    authorize_device,
    authorize_linked,
    current_pin,
    ensure_state,
    forget_peer,
    load_state,
    make_link_code,
    parse_link_code,
    parse_link_targets,
    peer_by_id,
    remember_linked_device,
    set_last_agreed,
    start_share,
    stop_share,
    upsert_peer,
)
from app.version import VERSION

bp = Blueprint("main", __name__)

OFFER_TYPE_CHOICES = [
    (OfferType.WELCOME, "Welcome"),
    (OfferType.RELOAD, "Reload"),
    (OfferType.RISK_FREE, "Risk-free"),
    (OfferType.ACCA_INSURANCE, "Acca insurance"),
    (OfferType.EXTRA_PLACE, "Extra place"),
    (OfferType.PRICE_BOOST, "Price boost"),
    (OfferType.OTHER, "Other"),
]

BET_TYPE_CHOICES = [
    (BetType.QUALIFYING, "Qualifying bet"),
    (BetType.FREE_BET_SNR, "Free bet (stake not returned)"),
    (BetType.FREE_BET_SR, "Free bet (stake returned)"),
    (BetType.MONEY_BACK, "Money back if bet loses"),
    (BetType.OTHER, "Other / manual"),
]


def _notify_linked() -> None:
    from app.live_sync import notify_after_save

    notify_after_save()


def _commit_and_sync(session: Session) -> None:
    session.commit()
    from app.settings import get as setting

    _notify_linked()
    if not setting("excel_sync"):
        return
    try:
        sync_workbook(session)
    except Exception as exc:  # noqa: BLE001
        flash(f"Saved, but Excel sync failed: {exc}", "error")


def _bearer_token() -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return (request.args.get("token") or "").strip()


@bp.before_request
def _block_remote_ui():
    from app.access import enforce_local_ui

    return enforce_local_ui()


def _app_port() -> int:
    from app.settings import get as setting

    return int(setting("port"))


def _parse_decimal(name: str, default: str = "0") -> Decimal:
    raw = (request.form.get(name) or default).strip().replace("£", "").replace(",", "")
    if raw == "":
        raw = default
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid number for {name.replace('_', ' ')}.") from exc


def _bookies(session: Session) -> list[Account]:
    return list(
        session.scalars(
            select(Account)
            .where(Account.type == AccountType.BOOKIE)
            .order_by(Account.name)
        )
    )


def _exchanges(session: Session) -> list[Account]:
    return list(
        session.scalars(
            select(Account)
            .where(Account.type == AccountType.EXCHANGE)
            .order_by(Account.name)
        )
    )


def _offers(session: Session) -> list[Offer]:
    return list(
        session.scalars(
            select(Offer)
            .options(selectinload(Offer.bookie), selectinload(Offer.bets))
            .order_by(Offer.created_at.desc())
        )
    )


def _form_context(session: Session) -> dict:
    return {
        "bookies": _bookies(session),
        "exchanges": _exchanges(session),
        "offers": _offers(session),
        "offer_types": OFFER_TYPE_CHOICES,
        "bet_types": BET_TYPE_CHOICES,
        "today": format_uk(date.today()),
    }


def _maybe_reconcile(session: Session) -> None:
    changed = bool(reconcile_offer_deposits(session))
    changed = bool(reconcile_settlement_sides(session)) or changed
    if changed:
        _commit_and_sync(session)


@bp.get("/")
def dashboard():
    session = get_session()
    _maybe_reconcile(session)
    stats = dashboard_stats(session)
    offer_rows = [offer_snapshot(offer) for offer in stats["in_progress_offers"]]
    show_spreadsheet_cta = session.scalars(select(Bet.id).limit(1)).first() is None
    return render_template(
        "dashboard.html",
        stats=stats,
        offer_rows=offer_rows,
        show_spreadsheet_cta=show_spreadsheet_cta,
        profit_chart=profit_series(session, range_key="1W"),
    )


def _optional_int(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if text.isdigit():
        return int(text)
    return None


@bp.get("/visualiser")
def visualiser_page():
    session = get_session()
    _maybe_reconcile(session)
    ctx = _form_context(session)
    return render_template(
        "visualiser.html",
        views=VIEWS,
        ranges=RANGES,
        **ctx,
    )


@bp.get("/api/charts")
def charts_api():
    session = get_session()
    return jsonify(
        visualiser_payload(
            session,
            view=(request.args.get("view") or "profit_time").strip(),
            range_key=(request.args.get("range") or "1W").strip(),
            start_raw=request.args.get("from"),
            end_raw=request.args.get("to"),
            bookie_id=_optional_int(request.args.get("bookie_id")),
            exchange_id=_optional_int(request.args.get("exchange_id")),
            bet_type=(request.args.get("bet_type") or "").strip() or None,
            offer_id=_optional_int(request.args.get("offer_id")),
            account_id=_optional_int(request.args.get("account_id")),
        )
    )


@bp.get("/calculator")
def calculator_page():
    session = get_session()
    ctx = _form_context(session)
    selected_offer_id = (request.args.get("offer_id") or "").strip()
    selected_bookie_id = (request.args.get("bookie_id") or "").strip()
    if selected_offer_id.isdigit():
        offer = session.get(Offer, int(selected_offer_id))
        if offer:
            selected_offer_id = str(offer.id)
            selected_bookie_id = selected_bookie_id or str(offer.bookie_id)
        else:
            selected_offer_id = ""
    ctx["selected_offer_id"] = selected_offer_id
    ctx["selected_bookie_id"] = selected_bookie_id or str(web_session.get("last_bookie_id") or "")
    from app.settings import get as setting

    selected_exchange = str(web_session.get("last_exchange_id") or "")
    if not selected_exchange:
        default_exchange = setting("default_exchange_id")
        if default_exchange:
            selected_exchange = str(default_exchange)
    if selected_exchange.isdigit() and session.get(Account, int(selected_exchange)) is None:
        selected_exchange = ""
    ctx["selected_exchange_id"] = selected_exchange
    return render_template("calculator.html", **ctx)


@bp.post("/api/calculate")
def api_calculate():
    data = request.get_json(force=True, silent=True) or {}
    try:
        override = data.get("lay_stake_override")
        if override in ("", None):
            override = None
        calc = calculate(
            bet_type=data.get("bet_type") or CalcBetType.QUALIFYING,
            back_stake=data.get("back_stake") or 0,
            back_odds=data.get("back_odds") or 0,
            lay_odds=data.get("lay_odds") or 0,
            commission_percent=data.get("commission_percent") or 0,
            cashback=data.get("cashback") or 0,
            lay_stake_override=override,
        )
        return jsonify(calc.as_dict())
    except (ValueError, InvalidOperation, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400


def _calculation_from_form():
    bet_type = request.form.get("bet_type") or BetType.QUALIFYING
    back_stake = _parse_decimal("back_stake")
    back_odds = _parse_decimal("back_odds")
    lay_odds = _parse_decimal("lay_odds")
    commission = _parse_decimal("commission_percent")
    cashback = _parse_decimal("cashback")
    override_raw = (request.form.get("lay_stake_override") or "").strip()
    override = Decimal(override_raw) if override_raw else None

    if bet_type == BetType.OTHER:
        lay_stake = _parse_decimal("lay_stake_override")
        liability = (
            (lay_stake * (lay_odds - 1)).quantize(Decimal("0.01"))
            if lay_odds
            else Decimal("0")
        )
        expected = _parse_decimal("expected_profit_manual")
        return {
            "bet_type": bet_type,
            "back_stake": back_stake,
            "back_odds": back_odds,
            "lay_odds": lay_odds,
            "commission_percent": commission,
            "cashback": cashback,
            "lay_stake": lay_stake,
            "liability": liability,
            "expected_profit": expected,
            "expected_bookie_back": expected,
            "expected_exchange_back": Decimal("0"),
            "expected_bookie_lay": expected,
            "expected_exchange_lay": Decimal("0"),
        }

    calc = calculate(
        bet_type=bet_type,
        back_stake=back_stake,
        back_odds=back_odds,
        lay_odds=lay_odds,
        commission_percent=commission,
        cashback=cashback,
        lay_stake_override=override,
    )
    return {
        "bet_type": bet_type,
        "back_stake": calc.back_stake,
        "back_odds": calc.back_odds,
        "lay_odds": calc.lay_odds,
        "commission_percent": calc.commission_percent,
        "cashback": calc.cashback,
        "lay_stake": calc.lay_stake,
        "liability": calc.liability,
        "expected_profit": calc.expected_profit,
        "expected_bookie_back": calc.if_back_wins.bookie,
        "expected_exchange_back": calc.if_back_wins.exchange,
        "expected_bookie_lay": calc.if_lay_wins.bookie,
        "expected_exchange_lay": calc.if_lay_wins.exchange,
    }


def _resolve_offer(session: Session, bookie_id: int) -> Offer | None:
    offer_id = (request.form.get("offer_id") or "").strip()
    if offer_id == "__new__":
        offer_id = ""
    new_name = (request.form.get("new_offer_name") or "").strip()
    if offer_id:
        offer = session.get(Offer, int(offer_id))
        if offer is None:
            raise ValueError("That offer no longer exists.")
        return offer
    if not new_name:
        return None
    deposit = _parse_decimal("offer_deposit")
    offer = Offer(
        name=new_name,
        type=request.form.get("offer_type") or OfferType.WELCOME,
        bookie_id=bookie_id,
        deposit_amount=deposit,
        free_funds=_parse_decimal("offer_free_funds"),
        notes=(request.form.get("offer_notes") or "").strip(),
    )
    session.add(offer)
    session.flush()
    sync_offer_deposit(session, offer, when=parse_uk(request.form.get("date_placed")))
    return offer


@bp.post("/calculator/log")
def log_bet():
    session = get_session()
    try:
        bookie_id = int(request.form.get("bookie_id") or 0)
        exchange_id = int(request.form.get("exchange_id") or 0)
        if not bookie_id or not exchange_id:
            raise ValueError("Choose both a bookie and an exchange.")
        numbers = _calculation_from_form()
        offer = _resolve_offer(session, bookie_id)
        placed_at = local_now()
        bet = Bet(
            offer_id=offer.id if offer else None,
            date_placed=parse_uk(request.form.get("date_placed")),
            placed_at=placed_at,
            event=(request.form.get("event") or "").strip(),
            market=(request.form.get("market") or "").strip(),
            notes=(request.form.get("notes") or "").strip(),
            bookie_id=bookie_id,
            exchange_id=exchange_id,
            status=BetStatus.PENDING,
            **numbers,
        )
        session.add(bet)
        web_session["last_bookie_id"] = bookie_id
        web_session["last_exchange_id"] = exchange_id
        _commit_and_sync(session)
        flash("Bet logged as pending.", "ok")
        if offer:
            return redirect(url_for("main.offer_detail", offer_id=offer.id))
        return redirect(url_for("main.bets"))
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.calculator_page"))


@bp.get("/offers")
def offers():
    session = get_session()
    rows = [offer_snapshot(offer) for offer in _offers(session)]
    return render_template("offers.html", rows=rows, **_form_context(session))


@bp.post("/offers")
def create_offer():
    session = get_session()
    try:
        bookie_id = int(request.form.get("bookie_id") or 0)
        name = (request.form.get("name") or "").strip()
        if not bookie_id or not name:
            raise ValueError("Offer name and bookie are required.")
        deposit = _parse_decimal("deposit_amount")
        offer = Offer(
            name=name,
            type=request.form.get("type") or OfferType.WELCOME,
            bookie_id=bookie_id,
            deposit_amount=deposit,
            free_funds=_parse_decimal("free_funds"),
            notes=(request.form.get("notes") or "").strip(),
        )
        session.add(offer)
        session.flush()
        sync_offer_deposit(session, offer)
        _commit_and_sync(session)
        flash("Offer created.", "ok")
        return redirect(url_for("main.offer_detail", offer_id=offer.id))
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.offers"))


@bp.get("/offers/<int:offer_id>")
def offer_detail(offer_id: int):
    session = get_session()
    offer = session.scalars(
        select(Offer)
        .options(selectinload(Offer.bets), selectinload(Offer.bookie))
        .where(Offer.id == offer_id)
    ).first()
    if offer is None:
        flash("Offer not found.", "error")
        return redirect(url_for("main.offers"))
    return render_template(
        "offer_detail.html",
        offer=offer,
        snap=offer_snapshot(offer),
        **_form_context(session),
    )


@bp.post("/offers/<int:offer_id>/delete")
def delete_offer(offer_id: int):
    session = get_session()
    offer = session.get(Offer, offer_id)
    if offer:
        for bet in list(offer.bets):
            bet.offer_id = None
        for transfer in list(offer.transfers):
            transfer.offer_id = None
        session.delete(offer)
        _commit_and_sync(session)
        flash("Offer deleted. Bets and deposits stay in the log.", "ok")
    return redirect(url_for("main.offers"))


@bp.get("/bets")
def bets():
    session = get_session()
    status = request.args.get("status") or "all"
    q = (request.args.get("q") or "").strip().lower()
    query = select(Bet).options(
        selectinload(Bet.bookie),
        selectinload(Bet.exchange),
        selectinload(Bet.offer),
    )
    if status == "pending":
        query = query.where(Bet.status == BetStatus.PENDING)
    elif status == "settled":
        query = query.where(Bet.status != BetStatus.PENDING)
    rows = list(session.scalars(query.order_by(Bet.date_placed.desc(), Bet.id.desc())))
    if q:
        rows = [
            bet
            for bet in rows
            if q in (bet.event or "").lower()
            or q in (bet.market or "").lower()
            or q in (bet.notes or "").lower()
            or q in bet.bookie.name.lower()
            or (bet.offer and q in bet.offer.name.lower())
        ]
    return render_template("bets.html", bets=rows, status=status, q=request.args.get("q") or "")


@bp.get("/bets/<int:bet_id>")
def bet_detail(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    if bet is None:
        flash("Bet not found.", "error")
        return redirect(url_for("main.bets"))
    suggested = suggested_settlement(bet)
    return render_template("bet_detail.html", bet=bet, suggested=suggested)


@bp.post("/bets/<int:bet_id>/settle")
def settle_bet(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    if bet is None:
        flash("Bet not found.", "error")
        return redirect(url_for("main.bets"))
    try:
        outcome = request.form.get("outcome") or ""
        if outcome not in {BetStatus.BACK_WON, BetStatus.LAY_WON, BetStatus.VOID}:
            raise ValueError("Choose how the bet settled.")
        suggested = suggested_settlement(bet)[outcome]
        bookie_over = request.form.get("actual_bookie_profit") not in (None, "")
        exchange_over = request.form.get("actual_exchange_profit") not in (None, "")
        net_over = request.form.get("actual_profit") not in (None, "")
        bookie_pl = (
            _parse_decimal("actual_bookie_profit", str(suggested["bookie"]))
            if bookie_over
            else suggested["bookie"]
        )
        exchange_pl = (
            _parse_decimal("actual_exchange_profit", str(suggested["exchange"]))
            if exchange_over
            else suggested["exchange"]
        )
        if net_over and not bookie_over and not exchange_over:
            net = _parse_decimal("actual_profit", str(suggested["net"]))
            bookie_pl = suggested["bookie"]
            exchange_pl = net - bookie_pl
        elif net_over and bookie_over and not exchange_over:
            net = _parse_decimal("actual_profit", str(suggested["net"]))
            exchange_pl = net - bookie_pl
        elif net_over and exchange_over and not bookie_over:
            net = _parse_decimal("actual_profit", str(suggested["net"]))
            bookie_pl = net - exchange_pl
        else:
            net = bookie_pl + exchange_pl
        bet.status = outcome
        bet.actual_bookie_profit = bookie_pl
        bet.actual_exchange_profit = exchange_pl
        bet.actual_profit = net
        bet.settled_at = local_now()
        if outcome == BetStatus.VOID and bet.is_free_bet:
            bet.free_bet_returned = request.form.get("free_bet_returned") == "1"
        else:
            bet.free_bet_returned = False
        _commit_and_sync(session)
        flash("Bet settled and Excel updated.", "ok")
        if bet.offer_id:
            return redirect(url_for("main.offer_detail", offer_id=bet.offer_id))
        return redirect(url_for("main.bets"))
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.bet_detail", bet_id=bet_id))


@bp.post("/bets/<int:bet_id>/reopen")
def reopen_bet(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    if bet:
        bet.status = BetStatus.PENDING
        bet.actual_profit = None
        bet.actual_bookie_profit = None
        bet.actual_exchange_profit = None
        bet.settled_at = None
        bet.free_bet_returned = False
        _commit_and_sync(session)
        flash("Bet reopened as pending.", "ok")
    return redirect(url_for("main.bet_detail", bet_id=bet_id))


@bp.post("/bets/<int:bet_id>/free-bet-returned")
def update_free_bet_returned(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    if bet is None:
        flash("Bet not found.", "error")
        return redirect(url_for("main.bets"))
    if bet.status != BetStatus.VOID or not bet.is_free_bet:
        flash("That only applies to voided free bets.", "error")
        return redirect(url_for("main.bet_detail", bet_id=bet_id))
    bet.free_bet_returned = request.form.get("free_bet_returned") == "1"
    _commit_and_sync(session)
    flash("Free-funds usage updated.", "ok")
    if bet.offer_id:
        return redirect(url_for("main.offer_detail", offer_id=bet.offer_id))
    return redirect(url_for("main.bet_detail", bet_id=bet_id))


@bp.post("/bets/<int:bet_id>/delete")
def delete_bet(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    offer_id = bet.offer_id if bet else None
    if bet:
        session.delete(bet)
        _commit_and_sync(session)
        flash("Bet deleted.", "ok")
    if offer_id:
        return redirect(url_for("main.offer_detail", offer_id=offer_id))
    return redirect(url_for("main.bets"))


@bp.get("/accounts")
def accounts():
    session = get_session()
    _maybe_reconcile(session)
    bookies = [account_snapshot(session, a) for a in _bookies(session)]
    exchanges = [account_snapshot(session, a) for a in _exchanges(session)]
    apply_sparklines(bookies + exchanges, account_sparklines(session))
    for snap in bookies + exchanges:
        snap.update(account_usage(session, snap["account"].id))
    transfers = list(
        session.scalars(
            select(Transfer)
            .options(selectinload(Transfer.account), selectinload(Transfer.offer))
            .order_by(Transfer.date.desc(), Transfer.id.desc())
            .limit(80)
        )
    )
    active_bookies = [
        snap
        for snap in bookies
        if snap["deposited"] or snap["withdrawn"] or snap["net_profit"] or snap["balance"]
    ]
    unused_bookies = [snap for snap in bookies if snap not in active_bookies]
    totals = {
        "balance": sum((snap["balance"] for snap in bookies + exchanges), Decimal("0")),
        "deposited": sum((snap["deposited"] for snap in bookies + exchanges), Decimal("0")),
        "net_profit": sum((snap["net_profit"] for snap in active_bookies), Decimal("0")),
    }
    return render_template(
        "accounts.html",
        bookies=bookies,
        active_bookies=active_bookies,
        unused_bookies=unused_bookies,
        exchanges=exchanges,
        transfers=transfers,
        totals=totals,
        today=format_uk(date.today()),
        accounts_chart=accounts_profit_bars(session),
    )


@bp.get("/accounts/<int:account_id>")
def account_detail(account_id: int):
    session = get_session()
    account = session.get(Account, account_id)
    if account is None:
        flash("Account not found.", "error")
        return redirect(url_for("main.accounts"))
    snap = account_snapshot(session, account)
    snap.update(account_usage(session, account.id))
    bet_filter = Bet.bookie_id == account.id if account.is_bookie else Bet.exchange_id == account.id
    bets = list(
        session.scalars(
            select(Bet)
            .options(
                selectinload(Bet.bookie),
                selectinload(Bet.exchange),
                selectinload(Bet.offer),
            )
            .where(bet_filter)
            .order_by(Bet.date_placed.desc(), Bet.id.desc())
        )
    )
    offers = []
    if account.is_bookie:
        offers = [
            offer_snapshot(offer)
            for offer in session.scalars(
                select(Offer)
                .options(selectinload(Offer.bets), selectinload(Offer.bookie))
                .where(Offer.bookie_id == account.id)
                .order_by(Offer.created_at.desc())
            )
        ]
    transfers = list(
        session.scalars(
            select(Transfer)
            .options(selectinload(Transfer.offer))
            .where(Transfer.account_id == account.id)
            .order_by(Transfer.date.desc(), Transfer.id.desc())
        )
    )
    pending_expected = sum(
        (bet.expected_profit or 0) for bet in bets if bet.status == BetStatus.PENDING
    )
    return render_template(
        "account_detail.html",
        account=account,
        snap=snap,
        bets=bets,
        offers=offers,
        transfers=transfers,
        pending_expected=pending_expected,
        today=format_uk(date.today()),
        profit_chart=visualiser_payload(session, view="profit_time", account_id=account.id),
        cash_chart=visualiser_payload(session, view="cashflow", account_id=account.id),
        offer_chart=visualiser_payload(session, view="by_offer", account_id=account.id)
        if account.is_bookie
        else None,
    )


@bp.post("/accounts")
def create_account():
    session = get_session()
    try:
        name = (request.form.get("name") or "").strip()
        if not name:
            raise ValueError("Account name is required.")
        kind = (request.form.get("type") or "").strip()
        if kind not in {AccountType.BOOKIE, AccountType.EXCHANGE}:
            raise ValueError("Choose whether this is a bookie or an exchange.")
        commission = _parse_decimal("commission_percent")
        session.add(
            Account(name=name, type=kind, commission_percent=commission)
        )
        _commit_and_sync(session)
        flash(f"{name} added as an {kind}.", "ok")
    except IntegrityError:
        session.rollback()
        flash("An account with that name already exists.", "error")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.accounts"))


@bp.post("/accounts/<int:account_id>/update")
def update_account(account_id: int):
    session = get_session()
    account = session.get(Account, account_id)
    if account is None:
        flash("Account not found.", "error")
        return redirect(url_for("main.accounts"))
    try:
        kind = (request.form.get("type") or account.type).strip()
        if kind not in {AccountType.BOOKIE, AccountType.EXCHANGE}:
            raise ValueError("Type must be bookie or exchange.")
        new_name = (request.form.get("name") or "").strip()
        if new_name:
            account.name = new_name
        account.type = kind
        account.commission_percent = _parse_decimal("commission_percent")
        _commit_and_sync(session)
        flash(f"{account.name} updated.", "ok")
        return redirect(url_for("main.account_detail", account_id=account.id))
    except IntegrityError:
        session.rollback()
        flash("An account with that name already exists.", "error")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.account_detail", account_id=account.id))


@bp.post("/accounts/<int:account_id>/delete")
def delete_account(account_id: int):
    session = get_session()
    account = session.get(Account, account_id)
    if account is None:
        flash("Account not found.", "error")
        return redirect(url_for("main.accounts"))
    usage = account_usage(session, account.id)
    if not usage["can_delete"]:
        bits = []
        if usage["bets"]:
            bits.append(f"{usage['bets']} bet{'s' if usage['bets'] != 1 else ''}")
        if usage["offers"]:
            bits.append(f"{usage['offers']} offer{'s' if usage['offers'] != 1 else ''}")
        if usage["transfers"]:
            bits.append(f"{usage['transfers']} transfer{'s' if usage['transfers'] != 1 else ''}")
        flash(f"Can't delete {account.name} while it still has {', '.join(bits)}.", "error")
        return redirect(url_for("main.accounts"))
    name = account.name
    session.delete(account)
    _commit_and_sync(session)
    flash(f"{name} deleted.", "ok")
    return redirect(url_for("main.accounts"))


@bp.post("/accounts/<int:account_id>/transfer")
def add_transfer(account_id: int):
    session = get_session()
    account = session.get(Account, account_id)
    if account is None:
        flash("Account not found.", "error")
        return redirect(url_for("main.accounts"))
    try:
        kind = request.form.get("kind") or TransferKind.DEPOSIT
        if kind not in {TransferKind.OPENING, TransferKind.DEPOSIT, TransferKind.WITHDRAWAL}:
            raise ValueError("Unknown transfer type.")
        amount = _parse_decimal("amount")
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
        session.add(
            Transfer(
                account_id=account_id,
                kind=kind,
                amount=amount,
                date=parse_uk(request.form.get("date")),
                notes=(request.form.get("notes") or "").strip(),
            )
        )
        _commit_and_sync(session)
        flash(f"{kind.title()} of £{amount} recorded on {account.name}.", "ok")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
    if request.form.get("next") == "detail":
        return redirect(url_for("main.account_detail", account_id=account_id))
    return redirect(url_for("main.accounts"))


@bp.get("/export")
def export_page():
    from app import EXCEL_PATH

    session = get_session()
    path = sync_workbook(session)
    sheets = preview_workbook(path)
    slugs = {sheet["slug"] for sheet in sheets}
    requested = (request.args.get("sheet") or "dashboard").lower()
    active = requested if requested in slugs else (sheets[0]["slug"] if sheets else "")
    exists = path.exists()
    mtime = datetime.fromtimestamp(path.stat().st_mtime) if exists else None
    return render_template(
        "export.html",
        path=str(EXCEL_PATH),
        exists=exists,
        mtime=mtime,
        sheets=sheets,
        active_sheet=active,
    )


@bp.post("/export/import")
def import_spreadsheet():
    from app import DATA_DIR, EXCEL_PATH

    session = get_session()
    upload = request.files.get("workbook")
    if upload is None or not upload.filename:
        flash("Choose an .xlsx file to import.", "error")
        return redirect(url_for("main.export_page"))
    filename = upload.filename.lower()
    if not filename.endswith((".xlsx", ".xlsm")):
        flash("Upload an Excel .xlsx file.", "error")
        return redirect(url_for("main.export_page"))
    replace = request.form.get("replace") == "1"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        saved = DATA_DIR / "imported_source.xlsx"
        upload.save(saved)
        result = import_workbook(session, saved, replace=replace)
        _commit_and_sync(session)
        bits = [
            f"{result['counts']['bets']} bets",
            f"{result['counts']['offers']} offers",
            f"{result['counts']['transfers']} transfers",
        ]
        extra = f" New accounts: {', '.join(result['new_accounts'])}." if result["new_accounts"] else ""
        flash(
            f"Imported {', '.join(bits)} from {', '.join(result['sheets']) or 'the workbook'}.{extra}",
            "ok",
        )
        for warning in result["warnings"]:
            flash(warning, "error")
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        flash(f"Could not import that spreadsheet: {exc}", "error")
    return redirect(url_for("main.export_page"))


@bp.get("/export/download")
def download_excel():
    from app import EXCEL_PATH

    session = get_session()
    path = sync_workbook(session)
    return send_file(
        path,
        as_attachment=True,
        download_name="matched_betting.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _apply_numbers(bet: Bet, numbers: dict) -> None:
    for key, value in numbers.items():
        setattr(bet, key, value)


@bp.get("/bets/<int:bet_id>/edit")
def edit_bet_page(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    if bet is None:
        flash("Bet not found.", "error")
        return redirect(url_for("main.bets"))
    return render_template("bet_edit.html", bet=bet, **_form_context(session))


@bp.post("/bets/<int:bet_id>/edit")
def edit_bet(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    if bet is None:
        flash("Bet not found.", "error")
        return redirect(url_for("main.bets"))
    try:
        bookie_id = int(request.form.get("bookie_id") or 0)
        exchange_id = int(request.form.get("exchange_id") or 0)
        if not bookie_id or not exchange_id:
            raise ValueError("Choose both a bookie and an exchange.")
        numbers = _calculation_from_form()
        offer = _resolve_offer(session, bookie_id)
        bet.offer_id = offer.id if offer else None
        new_date = parse_uk(request.form.get("date_placed"))
        bet.placed_at = combine_date(bet.placed_at, new_date)
        bet.date_placed = new_date
        bet.event = (request.form.get("event") or "").strip()
        bet.market = (request.form.get("market") or "").strip()
        bet.notes = (request.form.get("notes") or "").strip()
        bet.bookie_id = bookie_id
        bet.exchange_id = exchange_id
        _apply_numbers(bet, numbers)
        _commit_and_sync(session)
        flash("Bet updated.", "ok")
        return redirect(url_for("main.bet_detail", bet_id=bet.id))
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.edit_bet_page", bet_id=bet_id))


@bp.post("/bets/<int:bet_id>/duplicate")
def duplicate_bet(bet_id: int):
    session = get_session()
    bet = session.get(Bet, bet_id)
    if bet is None:
        flash("Bet not found.", "error")
        return redirect(url_for("main.bets"))
    placed_at = local_now()
    copy = Bet(
        offer_id=bet.offer_id,
        date_placed=placed_at.date(),
        placed_at=placed_at,
        event=bet.event,
        market=bet.market,
        notes=bet.notes,
        bet_type=bet.bet_type,
        bookie_id=bet.bookie_id,
        exchange_id=bet.exchange_id,
        back_stake=bet.back_stake,
        back_odds=bet.back_odds,
        lay_stake=bet.lay_stake,
        lay_odds=bet.lay_odds,
        commission_percent=bet.commission_percent,
        cashback=bet.cashback,
        liability=bet.liability,
        expected_profit=bet.expected_profit,
        expected_bookie_back=bet.expected_bookie_back,
        expected_exchange_back=bet.expected_exchange_back,
        expected_bookie_lay=bet.expected_bookie_lay,
        expected_exchange_lay=bet.expected_exchange_lay,
        status=BetStatus.PENDING,
    )
    session.add(copy)
    _commit_and_sync(session)
    flash("Copied as a new pending bet. Tweak anything that changed.", "ok")
    return redirect(url_for("main.edit_bet_page", bet_id=copy.id))


@bp.post("/offers/<int:offer_id>/edit")
def edit_offer(offer_id: int):
    session = get_session()
    offer = session.get(Offer, offer_id)
    if offer is None:
        flash("Offer not found.", "error")
        return redirect(url_for("main.offers"))
    try:
        name = (request.form.get("name") or "").strip()
        bookie_id = int(request.form.get("bookie_id") or 0)
        if not name or not bookie_id:
            raise ValueError("Offer name and bookie are required.")
        offer.name = name
        offer.bookie_id = bookie_id
        offer.type = request.form.get("type") or offer.type
        offer.notes = (request.form.get("notes") or "").strip()
        offer.deposit_amount = _parse_decimal("deposit_amount")
        offer.free_funds = _parse_decimal("free_funds")
        sync_offer_deposit(session, offer)
        _commit_and_sync(session)
        flash("Offer updated.", "ok")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.offer_detail", offer_id=offer_id))


@bp.post("/transfers/<int:transfer_id>/edit")
def edit_transfer(transfer_id: int):
    session = get_session()
    transfer = session.get(Transfer, transfer_id)
    if transfer is None:
        flash("Transfer not found.", "error")
        return redirect(url_for("main.accounts"))
    try:
        kind = request.form.get("kind") or transfer.kind
        if kind not in {TransferKind.OPENING, TransferKind.DEPOSIT, TransferKind.WITHDRAWAL}:
            raise ValueError("Unknown transfer type.")
        amount = _parse_decimal("amount")
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
        transfer.kind = kind
        transfer.amount = amount
        transfer.date = parse_uk(request.form.get("date"))
        transfer.notes = (request.form.get("notes") or "").strip()
        if transfer.offer_id:
            offer = session.get(Offer, transfer.offer_id)
            if offer and kind == TransferKind.DEPOSIT:
                offer.deposit_amount = amount
            elif offer:
                offer.deposit_amount = Decimal("0.00")
                transfer.offer_id = None
        _commit_and_sync(session)
        flash("Transfer updated.", "ok")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.accounts"))


@bp.post("/transfers/<int:transfer_id>/delete")
def delete_transfer(transfer_id: int):
    session = get_session()
    transfer = session.get(Transfer, transfer_id)
    if transfer:
        if transfer.offer_id and transfer.kind == TransferKind.DEPOSIT:
            offer = session.get(Offer, transfer.offer_id)
            if offer:
                offer.deposit_amount = Decimal("0.00")
        session.delete(transfer)
        _commit_and_sync(session)
        flash("Transfer deleted.", "ok")
    return redirect(url_for("main.accounts"))


@bp.get("/sync")
def sync_page():
    from app.backups import list_backups
    from app.nat import reachability
    from app.settings import get as setting

    pin = current_pin()
    port = _app_port()
    state = load_state()
    reach = reachability(port)
    this_computer = {
        "nickname": state.get("nickname") or "This computer",
        "lan_host": reach.get("lan_ip") or "",
        "port": port,
    }
    return render_template(
        "sync.html",
        sharing=bool(pin),
        pin=pin,
        link_code=make_link_code(pin, port) if pin else "",
        lan_ip=None,
        reach=reach,
        this_computer=this_computer,
        peers=state.get("peers") or [],
        conflict=load_state().get("conflict"),
        backups=list_backups(),
        auto_sync=setting("auto_sync"),
    )


@bp.post("/sync/share/start")
def sync_share_start():
    from app.nat import refresh as nat_refresh

    start_share()
    try:
        nat_refresh(_app_port())
    except Exception:  # noqa: BLE001
        pass
    flash("Sharing is on. Paste this code on the other computer. Both apps need to be running.", "ok")
    return redirect(url_for("main.sync_page"))


@bp.post("/sync/share/stop")
def sync_share_stop():
    stop_share()
    flash("Sharing stopped.", "ok")
    return redirect(url_for("main.sync_page"))


def _remember_caller() -> None:
    try:
        port_raw = request.headers.get("X-MBD-Port") or request.args.get("peer_port") or ""
        port = int(port_raw) if str(port_raw).isdigit() else _app_port()
    except ValueError:
        port = _app_port()
    remember_linked_device(
        device_id=request.headers.get("X-MBD-Device-Id") or request.args.get("peer_id") or "",
        token=request.headers.get("X-MBD-Device-Token") or request.args.get("peer_token") or "",
        nickname=request.headers.get("X-MBD-Nickname") or "",
        lan_host=request.headers.get("X-MBD-Lan") or request.args.get("peer_lan") or "",
        port=port,
    )


def _sync_status_body(session, token: str):
    if not authorize_linked(token):
        abort(403)
    _remember_caller()
    from app.nat import reachability
    from app.sync import status_payload

    pin_ok = current_pin() is not None and token == current_pin()
    body = status_payload(session, include_token=pin_ok)
    body["port"] = _app_port()
    reach = reachability(_app_port())
    body["lan_ip"] = reach.get("lan_ip")
    body["wan_ip"] = reach.get("wan_ip") if reach.get("mapped") else None
    return body


@bp.get("/api/sync/status")
def sync_status_api():
    token = _bearer_token()
    return jsonify(_sync_status_body(get_session(), token))


@bp.get("/api/sync/snapshot")
def sync_snapshot_api():
    token = _bearer_token()
    if not authorize_linked(token):
        abort(403)
    session = get_session()
    snap = dump_snapshot(session)
    body = _sync_status_body(session, token)
    body["snapshot"] = snap
    return jsonify(body)


@bp.get("/api/sync/<pin>")
def sync_pull(pin: str):
    if not authorize_device(pin):
        abort(403)
    session = get_session()
    return jsonify(dump_snapshot(session))


@bp.post("/api/sync/hello")
def sync_hello():
    token = _bearer_token()
    if not authorize_device(token):
        abort(403)
    payload = request.get_json(silent=True) or {}
    if not payload.get("token") or not payload.get("device_id"):
        abort(400)
    if current_pin() and token == current_pin():
        from app.sync import allow_relink

        allow_relink(str(payload["device_id"]), str(payload["token"]))
    remember_linked_device(
        device_id=str(payload["device_id"]),
        token=str(payload["token"]),
        nickname=(payload.get("nickname") or "Paired computer").strip(),
        lan_host=str(payload.get("lan_host") or payload.get("host") or ""),
        port=int(payload.get("port") or _app_port()),
    )
    return jsonify({"ok": True, "device_id": ensure_state()["device_id"]})


@bp.post("/api/sync/push")
def sync_push_api():
    from app.live_sync import apply_push
    from app.settings import get as setting

    token = _bearer_token()
    if not authorize_linked(token):
        abort(403)
    _remember_caller()
    session = get_session()
    try:
        counts = apply_push(session, request.get_json(silent=True) or {})
        session.commit()
        if setting("excel_sync"):
            try:
                sync_workbook(session)
            except Exception:  # noqa: BLE001
                pass
        return jsonify({"ok": True, **counts})
    except ValueError as exc:
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/api/sync/freshness")
def sync_freshness_api():
    from app.live_sync import freshness

    return jsonify(freshness(get_session()))


@bp.post("/sync/join")
def sync_join():
    from urllib.error import HTTPError, URLError

    from app.live_sync import fetch_json, post_json
    from app.nat import reachability

    session = get_session()
    try:
        pin, hosts = parse_link_targets(request.form.get("code") or "")
        if pin.startswith("view."):
            flash("That is a friend viewer code. Paste it on the Friends page.", "error")
            return redirect(url_for("main.sync_page"))
        if not pin.isdigit() or len(pin) != 6:
            raise ValueError("The PIN at the start of the code should be 6 digits.")
        remote = fetch_json(hosts, "/api/sync/snapshot", pin)
        payload = remote.get("snapshot") if isinstance(remote.get("snapshot"), dict) else remote
        if "accounts" not in payload:
            payload = fetch_json(hosts, f"/api/sync/{pin}", pin)
        local = dump_snapshot(session)
        if would_shrink(local, payload) and request.form.get("confirm_shrink") != "1":
            flash(
                f"Replace {local['counts']['bets']} bets with {snapshot_counts_safe(payload)} "
                f"from the other computer? Tick confirm if you mean to.",
                "error",
            )
            return redirect(url_for("main.sync_page"))
        counts = apply_snapshot(session, payload, backup_why="before-sync")
        _commit_and_sync(session)
        token = remote.get("token") or pin
        port = int(remote.get("port") or hosts[0].rsplit(":", 1)[-1])
        lan = remote.get("lan_ip") or hosts[0].split(":")[0]
        wan = remote.get("wan_ip") or ""
        if "+" in (request.form.get("code") or "") and len(hosts) > 1:
            wan = wan or hosts[1].split(":")[0]
        me = ensure_state()
        adopt_pair_secret(str(remote.get("pair_secret") or ""))
        upsert_peer(
            {
                "device_id": remote.get("device_id") or hosts[0],
                "nickname": remote.get("nickname") or hosts[0],
                "token": token,
                "host": hosts[0],
                "lan_host": lan,
                "wan_host": wan,
                "port": port,
                "our_token": me["device_token"],
            }
        )
        set_last_agreed(payload.get("fingerprint") or dump_snapshot(session)["fingerprint"])
        reach = reachability(_app_port())
        hello_ok = False
        for _attempt in range(2):
            try:
                post_json(
                    hosts,
                    "/api/sync/hello",
                    {
                        "device_id": me["device_id"],
                        "nickname": me["nickname"],
                        "token": me["device_token"],
                        "lan_host": reach.get("lan_ip"),
                        "wan_host": reach.get("wan_ip") if reach.get("mapped") else None,
                        "port": _app_port(),
                        "host": f"{reach.get('lan_ip')}:{_app_port()}",
                    },
                    pin,
                )
                hello_ok = True
                break
            except Exception:  # noqa: BLE001
                continue
        if hello_ok:
            flash(
                f"Copied {counts['bets']} bets, {counts['offers']} offers, "
                f"{counts['accounts']} accounts. Both computers should now list each other.",
                "ok",
            )
        else:
            flash(
                f"Copied {counts['bets']} bets from the other computer, but it may not list this one yet. "
                "Leave sharing on over there and pull once more from here — or pull on that computer after it shows you under Paired computers.",
                "error",
            )
    except ValueError as exc:
        flash(str(exc), "error")
    except HTTPError:
        flash("That code was rejected. Is sharing still on over there?", "error")
    except URLError:
        flash(
            "This computer cannot dial that address. Start sharing here and paste this code on the "
            "other computer — the machine that can send will also fetch.",
            "error",
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        flash(f"Could not apply the snapshot: {exc}", "error")
    return redirect(url_for("main.sync_page"))


def snapshot_counts_safe(payload: dict) -> int:
    return len(payload.get("bets") or [])


@bp.post("/sync/forget/<peer_id>")
def sync_forget(peer_id: str):
    forget_peer(peer_id)
    flash("Unlinked. They will not reappear until you share a code again.", "ok")
    return redirect(url_for("main.sync_page"))


@bp.post("/sync/pull/<peer_id>")
def sync_pull_peer(peer_id: str):
    from app.live_sync import pull_peer

    session = get_session()
    peer = peer_by_id(peer_id)
    if not peer:
        flash("That computer is not paired.", "error")
        return redirect(url_for("main.sync_page"))
    try:
        counts = pull_peer(session, peer, force=request.form.get("force") == "1")
        _commit_and_sync(session)
        flash(f"Copied {counts['bets']} bets from {peer.get('nickname') or 'the other computer'}.", "ok")
        return redirect(url_for("main.sync_page"))
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception:
        session.rollback()
        flash("Could not copy their log.", "error")
    return redirect(url_for("main.sync_page"))


@bp.post("/sync/keep")
def sync_keep():
    session = get_session()
    set_last_agreed(dump_snapshot(session)["fingerprint"])
    flash("This computer’s log will be the one others pull.", "ok")
    return redirect(url_for("main.sync_page"))


@bp.post("/sync/snapshot/save")
def sync_snapshot_save():
    from app.backups import save_current

    save_current(get_session(), why="manual")
    flash("Snapshot saved. You can restore it from the list below.", "ok")
    return redirect(url_for("main.sync_page"))


@bp.post("/sync/snapshot/<backup_id>/restore")
def sync_snapshot_restore(backup_id: str):
    from app.backups import restore

    session = get_session()
    try:
        counts = restore(session, backup_id)
        _commit_and_sync(session)
        flash(
            f"Restored {counts['bets']} bets, {counts['offers']} offers, {counts['accounts']} accounts.",
            "ok",
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        flash(f"Could not restore that snapshot: {exc}", "error")
    return redirect(url_for("main.sync_page"))


@bp.post("/api/sync/pull")
def sync_pull_now():
    from app.live_sync import pull_peer

    session = get_session()
    payload = request.get_json(silent=True) or {}
    peer_id = payload.get("peer_id")
    peer = peer_by_id(peer_id) if peer_id else None
    if peer is None:
        peers = load_state().get("peers") or []
        peer = peers[0] if peers else None
    if not peer:
        return jsonify({"ok": False, "error": "No paired computer."}), 400
    try:
        counts = pull_peer(session, peer, force=bool(payload.get("force")))
        _commit_and_sync(session)
        return jsonify({"ok": True, "counts": counts})
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/sync/backup")
def sync_backup():
    session = get_session()
    payload = dump_snapshot(session)
    from flask import Response
    import json

    body = json.dumps(payload, indent=2)
    filename = f"matched-betting-backup-{date.today().isoformat()}.json"
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.post("/sync/restore")
def sync_restore():
    import json

    session = get_session()
    upload = request.files.get("backup")
    if upload is None or not upload.filename:
        flash("Choose a backup .json file.", "error")
        return redirect(url_for("main.sync_page"))
    try:
        payload = json.load(upload.stream)
        counts = apply_snapshot(session, payload, backup_why="before-restore")
        _commit_and_sync(session)
        flash(
            f"Restored {counts['bets']} bets, {counts['offers']} offers, {counts['accounts']} accounts.",
            "ok",
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        flash(f"Could not restore that backup: {exc}", "error")
    return redirect(url_for("main.sync_page"))


@bp.get("/friends")
def friends_page():
    from app.friends import account_name, invite_code, load_state as load_friends
    from app.nat import reachability

    port = _app_port()
    state = load_friends()
    invites = []
    for invite in state.get("invites") or []:
        invites.append({**invite, "code": invite_code(invite, port)})
    return render_template(
        "friends.html",
        invites=invites,
        friends=state.get("friends") or [],
        reach=reachability(port),
        view=None,
        live=False,
        last_available=None,
        fetch_error=None,
        nickname=account_name(),
    )


@bp.get("/friends/<friend_id>")
def friend_detail(friend_id: str):
    from app.friends import account_name, fetch_live, friend_by_id, invite_code, load_cache, load_state as load_friends, store_cache
    from app.nat import reachability

    friend = friend_by_id(friend_id)
    if not friend:
        flash("That friend is not on your list.", "error")
        return redirect(url_for("main.friends_page"))
    view = None
    live = False
    last_available = None
    fetch_error = None
    try:
        view = fetch_live(friend)
        store_cache(friend_id, view)
        live = True
    except Exception as exc:  # noqa: BLE001
        fetch_error = str(exc)
        cached = load_cache(friend_id)
        if cached:
            view = cached.get("payload")
            last_available = cached.get("fetched_at")
    port = _app_port()
    state = load_friends()
    invites = [{**invite, "code": invite_code(invite, port)} for invite in state.get("invites") or []]
    return render_template(
        "friends.html",
        invites=invites,
        friends=state.get("friends") or [],
        reach=reachability(port),
        view=view,
        live=live,
        last_available=last_available,
        fetch_error=fetch_error,
        selected=friend,
        nickname=account_name(),
    )


@bp.post("/friends/invite")
def friends_invite():
    from app.friends import create_invite
    from app.nat import refresh as nat_refresh

    create_invite((request.form.get("nickname") or "").strip())
    try:
        nat_refresh(_app_port())
    except Exception:  # noqa: BLE001
        pass
    _notify_linked()
    try:
        from app.live_friends import notify, publish_now

        publish_now()
        notify()
    except Exception:  # noqa: BLE001
        pass
    flash(
        "Viewer invite created. Same Wi‑Fi is direct; another house uses the internet mailbox "
        "while both apps are running. The invite also appears on your other linked computers.",
        "ok",
    )
    return redirect(url_for("main.friends_page"))


@bp.post("/friends/invite/<invite_id>/revoke")
def friends_revoke(invite_id: str):
    from app.friends import revoke_invite

    revoke_invite(invite_id)
    _notify_linked()
    flash("That viewer invite is no longer valid.", "ok")
    return redirect(url_for("main.friends_page"))


@bp.post("/friends/invite/stop")
def friends_stop():
    from app.friends import stop_all_invites

    stop_all_invites()
    _notify_linked()
    flash("All viewer invites stopped.", "ok")
    return redirect(url_for("main.friends_page"))


@bp.post("/friends/add")
def friends_add():
    from urllib.error import HTTPError, URLError

    from app.friends import fetch_live, parse_friend_code, store_cache, upsert_friend

    try:
        secret, hosts = parse_friend_code(request.form.get("code") or "")
        if hosts:
            lan = hosts[0].split(":")[0]
            port = int(hosts[0].rsplit(":", 1)[-1])
            wan = hosts[1].split(":")[0] if len(hosts) > 1 else ""
            label = hosts[0]
        else:
            lan, wan, port, label = "", "", 5050, "internet"
        friend = {
            "secret": secret,
            "nickname": (request.form.get("nickname") or label).strip() or "Friend",
            "host": hosts[0] if hosts else "",
            "lan_host": lan,
            "wan_host": wan,
            "port": port,
        }
        from app.friends import load_state as load_friends

        upsert_friend(friend)
        _notify_linked()
        # Re-read id after upsert
        saved = next(
            (item for item in load_friends()["friends"] if item.get("secret") == secret),
            friend,
        )
        try:
            view = fetch_live(saved)
            store_cache(saved["id"], view)
            if view.get("nickname"):
                upsert_friend({**saved, "nickname": view["nickname"]})
        except Exception as exc:  # noqa: BLE001
            flash(str(exc), "error")
            return redirect(url_for("main.friend_detail", friend_id=saved["id"]))
        flash("Friend added.", "ok")
        return redirect(url_for("main.friend_detail", friend_id=saved["id"]))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.friends_page"))


@bp.post("/friends/<friend_id>/forget")
def friends_forget(friend_id: str):
    from app.friends import forget_friend

    forget_friend(friend_id)
    _notify_linked()
    flash("Friend removed.", "ok")
    return redirect(url_for("main.friends_page"))


@bp.get("/api/friend/view")
def friend_view_api():
    from app.friends import account_name, allow_rate, encrypt_view, invite_by_secret, view_dto

    token = _bearer_token()
    if not token.startswith("view."):
        abort(403)
    secret = token[5:]
    invite = invite_by_secret(secret)
    if invite is None:
        abort(403)
    from app.access import client_ip

    if not allow_rate(client_ip() or "local"):
        abort(429)
    session = get_session()
    dto = view_dto(session, nickname=account_name())
    return jsonify({"ciphertext": encrypt_view(secret, dto)})


@bp.get("/settings")
def settings_page():
    from app.settings import load

    session = get_session()
    return render_template(
        "settings.html",
        settings=load(),
        exchanges=_exchanges(session),
    )


@bp.post("/settings")
def settings_save():
    from app.settings import parse_port, save

    try:
        port = parse_port(request.form.get("port"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.settings_page"))
    exchange_raw = (request.form.get("default_exchange_id") or "").strip()
    save(
        {
            "open_browser": request.form.get("open_browser") == "on",
            "update_on_start": request.form.get("update_on_start") == "on",
            "update_popup": request.form.get("update_popup") == "on",
            "allow_lan": request.form.get("allow_lan") == "on",
            "excel_sync": request.form.get("excel_sync") == "on",
            "auto_sync": request.form.get("auto_sync") == "on",
            "port": port,
            "default_exchange_id": int(exchange_raw) if exchange_raw.isdigit() else None,
        }
    )
    flash("Settings saved. Port and Wi‑Fi access apply the next time you Start.", "ok")
    return redirect(url_for("main.settings_page"))


@bp.get("/api/update-status")
def update_status():
    from app.live_update import status

    return jsonify(status())


@bp.post("/api/update-apply")
def update_apply():
    from app.live_update import apply_and_relaunch

    payload = request.get_json(silent=True) or {}
    return jsonify(apply_and_relaunch(requested=payload.get("latest")))
