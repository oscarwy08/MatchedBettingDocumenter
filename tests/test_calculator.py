from decimal import Decimal

import pytest

from app.calculator import CalcBetType, calculate, unmatched_back


def D(value) -> Decimal:
    return Decimal(str(value))


def assert_equalised(calc) -> None:
    delta = abs(calc.if_back_wins.total - calc.if_lay_wins.total)
    assert delta < Decimal("1e-12"), delta


def test_qualifying_lay_stake_formula_unrounded():
    calc = calculate(
        CalcBetType.QUALIFYING,
        back_stake=10,
        back_odds=2,
        lay_odds="2.10",
        commission_percent=2,
        round_to_pence=False,
    )
    expected_lay = (D(2) * D(10)) / (D("2.10") - D("0.02"))
    assert calc.lay_stake == expected_lay
    assert_equalised(calc)


def test_qualifying_rounded_to_pence():
    calc = calculate(
        CalcBetType.QUALIFYING,
        back_stake=10,
        back_odds=2,
        lay_odds="2.10",
        commission_percent=2,
    )
    assert calc.lay_stake == D("9.62")
    assert calc.liability == D("10.58")
    assert calc.if_back_wins.bookie == D("10.00")
    assert calc.if_back_wins.exchange == D("-10.58")
    assert calc.if_lay_wins.bookie == D("-10.00")
    assert calc.if_lay_wins.exchange == D("9.43")


def test_snr_lay_stake_formula_unrounded():
    calc = calculate(
        CalcBetType.FREE_BET_SNR,
        back_stake=30,
        back_odds="5.50",
        lay_odds="5.20",
        commission_percent=2,
        round_to_pence=False,
    )
    expected_lay = ((D("5.50") - 1) * D(30)) / (D("5.20") - D("0.02"))
    assert calc.lay_stake == expected_lay
    assert_equalised(calc)
    assert calc.if_lay_wins.bookie == D(0)


def test_snr_rounded_bookie_keeps_winnings_only():
    calc = calculate(
        CalcBetType.FREE_BET_SNR,
        back_stake=10,
        back_odds=5,
        lay_odds="5.20",
        commission_percent=2,
    )
    assert calc.if_back_wins.bookie == D("40.00")
    assert calc.if_lay_wins.bookie == D("0.00")
    assert calc.lay_stake == D("7.72")


def test_sr_matches_qualifying_formula():
    kwargs = dict(
        back_stake=20,
        back_odds="3.20",
        lay_odds="3.30",
        commission_percent=5,
        round_to_pence=False,
    )
    qb = calculate(CalcBetType.QUALIFYING, **kwargs)
    sr = calculate(CalcBetType.FREE_BET_SR, **kwargs)
    assert qb.lay_stake == sr.lay_stake
    assert qb.if_back_wins.total == sr.if_back_wins.total


def test_money_back_reduces_lay_stake_and_equalises():
    plain = calculate(
        CalcBetType.QUALIFYING,
        back_stake=10,
        back_odds=2,
        lay_odds="2.10",
        commission_percent=2,
        round_to_pence=False,
    )
    mb = calculate(
        CalcBetType.MONEY_BACK,
        back_stake=10,
        back_odds=2,
        lay_odds="2.10",
        commission_percent=2,
        cashback=5,
        round_to_pence=False,
    )
    expected_lay = (D(2) * D(10) - D(5)) / (D("2.10") - D("0.02"))
    assert mb.lay_stake == expected_lay
    assert mb.lay_stake < plain.lay_stake
    assert_equalised(mb)
    assert mb.if_lay_wins.bookie == D("-5.00")


def test_lay_stake_override_recalculates_profit():
    even = calculate(
        CalcBetType.QUALIFYING,
        back_stake=10,
        back_odds=2,
        lay_odds="2.10",
        commission_percent=2,
    )
    overlay = calculate(
        CalcBetType.QUALIFYING,
        back_stake=10,
        back_odds=2,
        lay_odds="2.10",
        commission_percent=2,
        lay_stake_override="12.00",
    )
    assert overlay.lay_stake_overridden is True
    assert overlay.lay_stake == D("12.00")
    assert overlay.if_back_wins.total != overlay.if_lay_wins.total
    assert overlay.if_lay_wins.total > even.if_lay_wins.total
    assert overlay.if_back_wins.total < even.if_back_wins.total


def test_liability_is_lay_stake_times_odds_minus_one():
    calc = calculate(
        CalcBetType.QUALIFYING,
        back_stake=10,
        back_odds="2.50",
        lay_odds="2.60",
        commission_percent=2,
        round_to_pence=False,
    )
    assert calc.liability == calc.lay_stake * (D("2.60") - 1)


def test_rejects_invalid_odds():
    with pytest.raises(ValueError):
        calculate(
            CalcBetType.QUALIFYING,
            back_stake=10,
            back_odds=1,
            lay_odds=2,
            commission_percent=2,
        )
    with pytest.raises(ValueError):
        calculate(
            CalcBetType.QUALIFYING,
            back_stake=10,
            back_odds=2,
            lay_odds="1.01",
            commission_percent=200,
        )


def test_unmatched_back_has_no_lay():
    calc = unmatched_back("acca", back_stake=10, back_odds=8)
    assert calc.lay_stake == Decimal("0.00")
    assert calc.liability == Decimal("0.00")
    assert calc.expected_profit == Decimal("0.00")
    assert calc.if_back_wins.bookie == Decimal("70.00")
    assert calc.if_lay_wins.bookie == Decimal("-10.00")
