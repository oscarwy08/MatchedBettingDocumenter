from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AccountType(StrEnum):
    BOOKIE = "bookie"
    EXCHANGE = "exchange"


class OfferType(StrEnum):
    WELCOME = "welcome"
    RELOAD = "reload"
    RISK_FREE = "risk_free"
    ACCA_INSURANCE = "acca_insurance"
    EXTRA_PLACE = "extra_place"
    PRICE_BOOST = "price_boost"
    OTHER = "other"


class BetType(StrEnum):
    QUALIFYING = "qualifying"
    FREE_BET_SNR = "free_bet_snr"
    FREE_BET_SR = "free_bet_sr"
    MONEY_BACK = "money_back"
    OTHER = "other"


class BetStatus(StrEnum):
    PENDING = "pending"
    BACK_WON = "back_won"
    LAY_WON = "lay_won"
    VOID = "void"


class TransferKind(StrEnum):
    OPENING = "opening"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


Money = Numeric(12, 2)
Odds = Numeric(10, 4)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    type: Mapped[str] = mapped_column(String(20))
    commission_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    transfers: Mapped[list[Transfer]] = relationship(back_populates="account")
    offers: Mapped[list[Offer]] = relationship(back_populates="bookie")
    back_bets: Mapped[list[Bet]] = relationship(
        back_populates="bookie", foreign_keys="Bet.bookie_id"
    )
    lay_bets: Mapped[list[Bet]] = relationship(
        back_populates="exchange", foreign_keys="Bet.exchange_id"
    )

    @property
    def is_bookie(self) -> bool:
        return self.type == AccountType.BOOKIE

    @property
    def is_exchange(self) -> bool:
        return self.type == AccountType.EXCHANGE


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(40), default=OfferType.WELCOME)
    bookie_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    deposit_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    free_funds: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    bookie: Mapped[Account] = relationship(back_populates="offers")
    bets: Mapped[list[Bet]] = relationship(back_populates="offer")
    transfers: Mapped[list[Transfer]] = relationship(back_populates="offer")

    @property
    def free_funds_used(self) -> Decimal:
        used = Decimal("0.00")
        for bet in self.bets:
            if bet.bet_type not in {BetType.FREE_BET_SNR, BetType.FREE_BET_SR}:
                continue
            if bet.status == BetStatus.VOID and bet.free_bet_returned:
                continue
            used += Decimal(str(bet.back_stake or 0))
        return used

    @property
    def status(self) -> str:
        funds = Decimal(str(self.free_funds or 0))
        if funds > 0:
            if self.free_funds_used >= funds:
                return "Used"
            return "In progress"
        if not self.bets:
            return "In progress"
        if any(bet.status == BetStatus.PENDING for bet in self.bets):
            return "In progress"
        return "Complete"


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id"), nullable=True)
    date_placed: Mapped[date] = mapped_column(Date, default=date.today)
    event: Mapped[str] = mapped_column(String(200), default="")
    market: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    bet_type: Mapped[str] = mapped_column(String(40), default=BetType.QUALIFYING)
    bookie_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    exchange_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    back_stake: Mapped[Decimal] = mapped_column(Money)
    back_odds: Mapped[Decimal] = mapped_column(Odds)
    lay_stake: Mapped[Decimal] = mapped_column(Money)
    lay_odds: Mapped[Decimal] = mapped_column(Odds)
    commission_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    cashback: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    liability: Mapped[Decimal] = mapped_column(Money)
    expected_profit: Mapped[Decimal] = mapped_column(Money)
    expected_bookie_back: Mapped[Decimal] = mapped_column(Money)
    expected_exchange_back: Mapped[Decimal] = mapped_column(Money)
    expected_bookie_lay: Mapped[Decimal] = mapped_column(Money)
    expected_exchange_lay: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20), default=BetStatus.PENDING)
    actual_profit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    actual_bookie_profit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    actual_exchange_profit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    free_bet_returned: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    offer: Mapped[Offer | None] = relationship(back_populates="bets")
    bookie: Mapped[Account] = relationship(
        back_populates="back_bets", foreign_keys=[bookie_id]
    )
    exchange: Mapped[Account] = relationship(
        back_populates="lay_bets", foreign_keys=[exchange_id]
    )

    @property
    def is_pending(self) -> bool:
        return self.status == BetStatus.PENDING

    @property
    def is_settled(self) -> bool:
        return self.status != BetStatus.PENDING

    @property
    def is_free_bet(self) -> bool:
        return self.bet_type in {BetType.FREE_BET_SNR, BetType.FREE_BET_SR}


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Money)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    notes: Mapped[str] = mapped_column(Text, default="")
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    account: Mapped[Account] = relationship(back_populates="transfers")
    offer: Mapped[Offer | None] = relationship(back_populates="transfers")
