"""Matched-betting calculator using Matched Betting Blog formulas.

Commission is entered as a percent (e.g. 2 for Smarkets 2%) and converted
to a decimal internally. Lay stakes are rounded to the nearest penny unless
``round_to_pence`` is False (used by tests to check algebraic identity).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum


TWOPLACE = Decimal("0.01")
ZERO = Decimal("0.00")
ONE = Decimal("1")
HUNDRED = Decimal("100")


class CalcBetType(StrEnum):
    QUALIFYING = "qualifying"
    FREE_BET_SNR = "free_bet_snr"
    FREE_BET_SR = "free_bet_sr"
    MONEY_BACK = "money_back"


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value: Decimal | int | float | str) -> Decimal:
    return to_decimal(value).quantize(TWOPLACE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SideResult:
    bookie: Decimal
    exchange: Decimal

    @property
    def total(self) -> Decimal:
        return self.bookie + self.exchange


@dataclass(frozen=True)
class Calculation:
    bet_type: CalcBetType
    back_stake: Decimal
    back_odds: Decimal
    lay_odds: Decimal
    commission_percent: Decimal
    cashback: Decimal
    lay_stake: Decimal
    liability: Decimal
    if_back_wins: SideResult
    if_lay_wins: SideResult
    expected_profit: Decimal
    lay_stake_overridden: bool

    def as_dict(self) -> dict:
        return {
            "bet_type": self.bet_type.value,
            "back_stake": _fmt(self.back_stake),
            "back_odds": _fmt(self.back_odds, extras=2),
            "lay_odds": _fmt(self.lay_odds, extras=2),
            "commission_percent": _fmt(self.commission_percent, extras=2),
            "cashback": _fmt(self.cashback),
            "lay_stake": _fmt(self.lay_stake),
            "liability": _fmt(self.liability),
            "if_back_wins": {
                "bookie": _fmt(self.if_back_wins.bookie),
                "exchange": _fmt(self.if_back_wins.exchange),
                "total": _fmt(self.if_back_wins.total),
            },
            "if_lay_wins": {
                "bookie": _fmt(self.if_lay_wins.bookie),
                "exchange": _fmt(self.if_lay_wins.exchange),
                "total": _fmt(self.if_lay_wins.total),
            },
            "expected_profit": _fmt(self.expected_profit),
            "lay_stake_overridden": self.lay_stake_overridden,
        }


def _fmt(value: Decimal, extras: int = 0) -> str:
    places = 2 + extras
    q = Decimal("1").scaleb(-places)
    return str(value.quantize(q, rounding=ROUND_HALF_UP))


def _optimal_lay_stake(
    bet_type: CalcBetType,
    back_stake: Decimal,
    back_odds: Decimal,
    lay_odds: Decimal,
    commission: Decimal,
    cashback: Decimal,
) -> Decimal:
    divisor = lay_odds - commission
    if divisor <= 0:
        raise ValueError("Lay odds must be greater than commission.")

    if bet_type is CalcBetType.FREE_BET_SNR:
        numerator = (back_odds - ONE) * back_stake
    elif bet_type is CalcBetType.MONEY_BACK:
        numerator = back_odds * back_stake - cashback
    else:
        # Qualifying and stake-returned free bets share the same formula.
        numerator = back_odds * back_stake

    if numerator < 0:
        raise ValueError("Cashback cannot exceed back stake times back odds.")
    return numerator / divisor


def _bookie_outcomes(
    bet_type: CalcBetType,
    back_stake: Decimal,
    back_odds: Decimal,
    cashback: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return (bookie if back wins, bookie if lay wins). Cashback is bookie-side."""
    if bet_type is CalcBetType.FREE_BET_SNR:
        return back_stake * (back_odds - ONE), ZERO
    if bet_type is CalcBetType.MONEY_BACK:
        return back_stake * (back_odds - ONE), -back_stake + cashback
    return back_stake * (back_odds - ONE), -back_stake


def calculate(
    bet_type: CalcBetType | str,
    back_stake: Decimal | int | float | str,
    back_odds: Decimal | int | float | str,
    lay_odds: Decimal | int | float | str,
    commission_percent: Decimal | int | float | str,
    cashback: Decimal | int | float | str = 0,
    lay_stake_override: Decimal | int | float | str | None = None,
    round_to_pence: bool = True,
) -> Calculation:
    kind = CalcBetType(bet_type)
    stake = to_decimal(back_stake)
    b_odds = to_decimal(back_odds)
    l_odds = to_decimal(lay_odds)
    comm_pct = to_decimal(commission_percent)
    extra = to_decimal(cashback)
    commission = comm_pct / HUNDRED

    if stake < 0:
        raise ValueError("Back stake must be zero or positive.")
    if b_odds <= 1:
        raise ValueError("Back odds must be greater than 1.")
    if l_odds <= 1:
        raise ValueError("Lay odds must be greater than 1.")
    if comm_pct < 0:
        raise ValueError("Commission cannot be negative.")
    if extra < 0:
        raise ValueError("Cashback cannot be negative.")

    overridden = lay_stake_override is not None and str(lay_stake_override) != ""
    if overridden:
        lay_stake = to_decimal(lay_stake_override)
        if lay_stake < 0:
            raise ValueError("Lay stake cannot be negative.")
    else:
        lay_stake = _optimal_lay_stake(kind, stake, b_odds, l_odds, commission, extra)

    if round_to_pence:
        lay_stake = money(lay_stake)

    liability = lay_stake * (l_odds - ONE)
    if round_to_pence:
        liability = money(liability)

    bookie_back, bookie_lay = _bookie_outcomes(kind, stake, b_odds, extra)
    exchange_back = -liability
    exchange_lay = lay_stake * (ONE - commission)

    if round_to_pence:
        bookie_back = money(bookie_back)
        bookie_lay = money(bookie_lay)
        exchange_back = money(exchange_back)
        exchange_lay = money(exchange_lay)
        stake = money(stake)
        extra = money(extra)

    back_side = SideResult(bookie=bookie_back, exchange=exchange_back)
    lay_side = SideResult(bookie=bookie_lay, exchange=exchange_lay)
    expected = (back_side.total + lay_side.total) / Decimal("2")
    if round_to_pence:
        expected = money(expected)

    return Calculation(
        bet_type=kind,
        back_stake=stake,
        back_odds=b_odds,
        lay_odds=l_odds,
        commission_percent=comm_pct,
        cashback=extra,
        lay_stake=lay_stake,
        liability=liability,
        if_back_wins=back_side,
        if_lay_wins=lay_side,
        expected_profit=expected,
        lay_stake_overridden=overridden,
    )
