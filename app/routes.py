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
from app.dates import format_uk, parse_uk
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
from app.services import account_snapshot, dashboard_stats, offer_snapshot
from app.snapshot import apply_snapshot, dump_snapshot
from app.sync import current_pin, make_link_code, parse_link_code, start_share, stop_share
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


def _commit_and_sync(session: Session) -> None:
    session.commit()
    try:
        sync_workbook(session)
    except Exception as exc:  # noqa: BLE001
        flash(f"Saved, but Excel sync failed: {exc}", "error")


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


@bp.get("/")
def dashboard():
    session = get_session()
    stats = dashboard_stats(session)
    offer_rows = [offer_snapshot(offer) for offer in stats["in_progress_offers"]]
    show_spreadsheet_cta = session.scalars(select(Bet.id).limit(1)).first() is None
    return render_template(
        "dashboard.html",
        stats=stats,
        offer_rows=offer_rows,
        show_spreadsheet_cta=show_spreadsheet_cta,
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
    ctx["selected_exchange_id"] = str(web_session.get("last_exchange_id") or "")
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
    if deposit > 0:
        session.add(
            Transfer(
                account_id=bookie_id,
                kind=TransferKind.DEPOSIT,
                amount=deposit,
                date=parse_uk(request.form.get("date_placed")),
                notes=f"Deposit for {new_name}",
                offer_id=offer.id,
            )
        )
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
        bet = Bet(
            offer_id=offer.id if offer else None,
            date_placed=parse_uk(request.form.get("date_placed")),
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
        if deposit > 0:
            session.add(
                Transfer(
                    account_id=bookie_id,
                    kind=TransferKind.DEPOSIT,
                    amount=deposit,
                    notes=f"Deposit for {name}",
                    offer_id=offer.id,
                )
            )
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
        session.delete(offer)
        _commit_and_sync(session)
        flash("Offer deleted.", "ok")
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
    suggested = _suggested_settlement(bet)
    return render_template("bet_detail.html", bet=bet, suggested=suggested)


def _suggested_settlement(bet: Bet) -> dict:
    return {
        BetStatus.BACK_WON: {
            "bookie": bet.expected_bookie_back,
            "exchange": bet.expected_exchange_back,
            "net": bet.expected_bookie_back + bet.expected_exchange_back,
        },
        BetStatus.LAY_WON: {
            "bookie": bet.expected_bookie_lay,
            "exchange": bet.expected_exchange_lay,
            "net": bet.expected_bookie_lay + bet.expected_exchange_lay,
        },
        BetStatus.VOID: {
            "bookie": Decimal("0.00"),
            "exchange": Decimal("0.00"),
            "net": Decimal("0.00"),
        },
    }


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
        suggested = _suggested_settlement(bet)[outcome]
        bookie_pl = (
            _parse_decimal("actual_bookie_profit", str(suggested["bookie"]))
            if request.form.get("actual_bookie_profit") not in (None, "")
            else suggested["bookie"]
        )
        exchange_pl = (
            _parse_decimal("actual_exchange_profit", str(suggested["exchange"]))
            if request.form.get("actual_exchange_profit") not in (None, "")
            else suggested["exchange"]
        )
        net = (
            _parse_decimal("actual_profit", str(suggested["net"]))
            if request.form.get("actual_profit") not in (None, "")
            else bookie_pl + exchange_pl
        )
        bet.status = outcome
        bet.actual_bookie_profit = bookie_pl
        bet.actual_exchange_profit = exchange_pl
        bet.actual_profit = net
        bet.settled_at = datetime.now()
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
    bookies = [account_snapshot(session, a) for a in _bookies(session)]
    exchanges = [account_snapshot(session, a) for a in _exchanges(session)]
    transfers = list(
        session.scalars(
            select(Transfer)
            .options(selectinload(Transfer.account), selectinload(Transfer.offer))
            .order_by(Transfer.date.desc(), Transfer.id.desc())
            .limit(80)
        )
    )
    return render_template(
        "accounts.html",
        bookies=bookies,
        exchanges=exchanges,
        transfers=transfers,
        today=format_uk(date.today()),
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
    except IntegrityError:
        session.rollback()
        flash("An account with that name already exists.", "error")
    except (ValueError, InvalidOperation) as exc:
        flash(str(exc), "error")
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
        bet.date_placed = parse_uk(request.form.get("date_placed"))
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
    copy = Bet(
        offer_id=bet.offer_id,
        date_placed=date.today(),
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
        session.delete(transfer)
        _commit_and_sync(session)
        flash("Transfer deleted.", "ok")
    return redirect(url_for("main.accounts"))


@bp.get("/sync")
def sync_page():
    pin = current_pin()
    return render_template(
        "sync.html",
        sharing=bool(pin),
        pin=pin,
        link_code=make_link_code(pin) if pin else "",
        lan_ip=None,
    )


@bp.post("/sync/share/start")
def sync_share_start():
    start_share()
    flash("Sharing is on. Enter this code on the other computer. Same Wi‑Fi, both apps running.", "ok")
    return redirect(url_for("main.sync_page"))


@bp.post("/sync/share/stop")
def sync_share_stop():
    stop_share()
    flash("Sharing stopped.", "ok")
    return redirect(url_for("main.sync_page"))


@bp.get("/api/sync/<pin>")
def sync_pull(pin: str):
    if current_pin() is None or pin != current_pin():
        abort(403)
    session = get_session()
    return jsonify(dump_snapshot(session))


@bp.post("/sync/join")
def sync_join():
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    session = get_session()
    try:
        pin, host = parse_link_code(request.form.get("code") or "")
        req = Request(
            f"http://{host}/api/sync/{pin}",
            headers={"Accept": "application/json"},
        )
        with urlopen(req, timeout=12) as resp:
            payload = json.load(resp)
        counts = apply_snapshot(session, payload)
        _commit_and_sync(session)
        flash(
            f"Copied {counts['bets']} bets, {counts['offers']} offers, "
            f"{counts['accounts']} accounts from the other computer.",
            "ok",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    except HTTPError as exc:
        flash("That code was rejected. Is sharing still on over there?", "error")
    except URLError:
        flash("Could not reach the other computer. Same Wi‑Fi? Firewall allowing port 5050?", "error")
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        flash(f"Could not apply the snapshot: {exc}", "error")
    return redirect(url_for("main.sync_page"))


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
        counts = apply_snapshot(session, payload)
        _commit_and_sync(session)
        flash(
            f"Restored {counts['bets']} bets, {counts['offers']} offers, {counts['accounts']} accounts.",
            "ok",
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        flash(f"Could not restore that backup: {exc}", "error")
    return redirect(url_for("main.sync_page"))
