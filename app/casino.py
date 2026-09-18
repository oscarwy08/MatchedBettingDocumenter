"""Expected value for casino playthrough, bonuses, and free spins.

Formulas match Matched Betting Blog's casino offer guide: expected loss is
the amount wagered times (1 − RTP). Free-spin winnings are stake-not-returned.
Wagering on those winnings (or on a bonus) is a further playthrough cost.
A max cashout cap is applied last. Actual results vary; settle with what you
finished with.
"""

from __future__ import annotations

from decimal import Decimal

from app.calculator import ZERO, money, to_decimal

HUNDRED = Decimal("100")
ONE = Decimal("1")


def _rtp(percent: Decimal | int | float | str) -> Decimal:
    value = to_decimal(percent)
    if value <= 0 or value > HUNDRED:
        raise ValueError("RTP must be between 0 and 100.")
    return value


def playthrough_cost(
    amount: Decimal | int | float | str,
    rtp_percent: Decimal | int | float | str,
) -> Decimal:
    """Expected loss from wagering ``amount`` at ``rtp_percent``."""
    stake = money(amount)
    if stake < 0:
        raise ValueError("Wager must be zero or positive.")
    rtp = _rtp(rtp_percent)
    return money(stake * (ONE - rtp / HUNDRED))


def qualifying_ev(
    stake: Decimal | int | float | str,
    rtp_percent: Decimal | int | float | str,
) -> Decimal:
    """Expected P&L of a casino qualifying wager (usually a small loss)."""
    return money(-playthrough_cost(stake, rtp_percent))


def bonus_ev(
    bonus: Decimal | int | float | str,
    wagering: Decimal | int | float | str,
    rtp_percent: Decimal | int | float | str,
    max_cashout: Decimal | int | float | str | None = None,
) -> Decimal:
    """Expected cash from a bonus after wagering, e.g. £20 at 5× and 97.3% RTP → £17.30."""
    gift = money(bonus)
    if gift < 0:
        raise ValueError("Bonus must be zero or positive.")
    times = to_decimal(wagering or 0)
    if times < 0:
        raise ValueError("Wagering cannot be negative.")
    if times == 0:
        cash = gift
    else:
        cash = money(gift - playthrough_cost(gift * times, rtp_percent))
    return _cap(cash, max_cashout)


def spins_ev(
    count: Decimal | int | float | str,
    value: Decimal | int | float | str,
    rtp_percent: Decimal | int | float | str,
    wagering: Decimal | int | float | str = 0,
    max_cashout: Decimal | int | float | str | None = None,
    clear_rtp: Decimal | int | float | str | None = None,
) -> dict:
    """Expected value of free spins. Face is count × value; gross is face × RTP."""
    spins = to_decimal(count)
    if spins < 0 or spins != spins.to_integral_value():
        raise ValueError("Spin count must be a whole number.")
    coin = money(value)
    if coin < 0:
        raise ValueError("Spin value must be zero or positive.")
    face = money(spins * coin)
    rtp = _rtp(rtp_percent)
    gross = money(face * rtp / HUNDRED)
    times = to_decimal(wagering or 0)
    if times < 0:
        raise ValueError("Wagering cannot be negative.")
    if times == 0:
        cash = gross
    else:
        clear = rtp if clear_rtp in (None, "") else _rtp(clear_rtp)
        cash = money(gross - playthrough_cost(gross * times, clear))
    cash = _cap(cash, max_cashout)
    return {
        "count": int(spins),
        "value": coin,
        "face": face,
        "rtp": rtp,
        "gross": gross,
        "wagering": times,
        "cash": cash,
    }


def offer_ev(
    *,
    casino_wager: Decimal | int | float | str = 0,
    casino_rtp: Decimal | int | float | str = 0,
    spin_count: Decimal | int | float | str = 0,
    spin_value: Decimal | int | float | str = 0,
    spin_rtp: Decimal | int | float | str = 0,
    bonus: Decimal | int | float | str = 0,
    wagering: Decimal | int | float | str = 0,
    bonus_rtp: Decimal | int | float | str = 0,
    max_cashout: Decimal | int | float | str | None = None,
) -> dict:
    """Combine qualifying playthrough, optional bonus, and optional free spins."""
    qual_stake = money(casino_wager or 0)
    qual_rtp = to_decimal(casino_rtp or 0)
    qual = qualifying_ev(qual_stake, qual_rtp) if qual_stake and qual_rtp else ZERO
    gift = money(bonus or 0)
    wr = to_decimal(wagering or 0)
    clear = to_decimal(bonus_rtp or 0) or qual_rtp
    bonus_cash = bonus_ev(gift, wr, clear, max_cashout) if gift and wr and clear else (
        gift if gift and wr == 0 else ZERO
    )
    spin_rtp_val = to_decimal(spin_rtp or 0)
    spins = (
        spins_ev(spin_count, spin_value, spin_rtp_val, wr if gift == 0 else 0, max_cashout, spin_rtp_val)
        if to_decimal(spin_count or 0) and to_decimal(spin_value or 0) and spin_rtp_val
        else {
            "count": 0,
            "value": ZERO,
            "face": ZERO,
            "rtp": ZERO,
            "gross": ZERO,
            "wagering": ZERO,
            "cash": ZERO,
        }
    )
    net = money(qual + bonus_cash + spins["cash"])
    return {
        "qualifying": qual,
        "bonus": bonus_cash,
        "spins": spins["cash"],
        "spins_face": spins["face"],
        "net": net,
    }


def _cap(cash: Decimal, max_cashout: Decimal | int | float | str | None) -> Decimal:
    if max_cashout in (None, ""):
        return cash
    cap = money(max_cashout)
    if cap <= 0:
        return cash
    return money(min(cash, cap))


def cashout_from_profit(
    bet_type: str,
    stake: Decimal | int | float | str,
    profit: Decimal | int | float | str,
) -> Decimal:
    """Balance after the slots given logged P&L. Free spins are stake-not-returned."""
    p = money(profit)
    if bet_type in {"free_spins", "FREE_SPINS"}:
        return p
    return money(money(stake or 0) + p)


def profit_from_cashout(
    bet_type: str,
    stake: Decimal | int | float | str,
    cashout: Decimal | int | float | str,
) -> Decimal:
    """P&L from the balance you finished with."""
    c = money(cashout)
    if bet_type in {"free_spins", "FREE_SPINS"}:
        return c
    return money(c - money(stake or 0))


def actuals(
    bet_type: str,
    stake: Decimal | int | float | str = 0,
    *,
    cashout: Decimal | int | float | str | None = None,
    profit: Decimal | int | float | str | None = None,
) -> dict:
    """Either box is enough. Profit is what the log stores; cashout is the slot balance."""
    if profit not in (None, ""):
        p = money(profit)
        return {"cashout": cashout_from_profit(bet_type, stake, p), "profit": p}
    if cashout not in (None, ""):
        c = money(cashout)
        return {"cashout": c, "profit": profit_from_cashout(bet_type, stake, c)}
    raise ValueError("Enter what you cashed out or the profit.")


def result_dict(
    *,
    bet_type: str,
    stake: Decimal,
    expected_profit: Decimal,
    expected_return: Decimal | None = None,
    rtp: Decimal | None = None,
) -> dict:
    """Shape the live calculator and log payload like a matched Calculation."""
    profit = money(expected_profit)
    ret = money(expected_return if expected_return is not None else (stake + profit))
    return {
        "bet_type": bet_type,
        "back_stake": _fmt(stake),
        "back_odds": "1.0000",
        "lay_odds": "0.0000",
        "commission_percent": "0.00",
        "cashback": "0.00",
        "lay_stake": "0.00",
        "liability": "0.00",
        "if_back_wins": {
            "bookie": _fmt(profit),
            "exchange": "0.00",
            "total": _fmt(profit),
        },
        "if_lay_wins": {
            "bookie": _fmt(profit),
            "exchange": "0.00",
            "total": _fmt(profit),
        },
        "expected_profit": _fmt(profit),
        "expected_return": _fmt(ret),
        "lay_stake_overridden": False,
    }


def _fmt(value: Decimal, extras: int = 0) -> str:
    from app.calculator import _fmt as calc_fmt

    return calc_fmt(value, extras=extras)
