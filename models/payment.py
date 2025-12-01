from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Integer, Numeric, Text, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .scheduling import PrivateSession, Trainer


class Payment(Base):
    __tablename__ = "payment"

    payment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.member_id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    private_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("private_session.session_id"),
        nullable=True,
    )

    # optional relationship if you ever want it; safe without back_populates
    member = relationship("Member")
    private_session: Mapped[Optional["PrivateSession"]] = relationship(
        "PrivateSession",
        back_populates="payments",
    )

    def __repr__(self) -> str:
        return f"<Payment id={self.payment_id} member_id={self.member_id} amount={self.amount}>"


class BillingItem(Base):
    __tablename__ = "billing_item"

    billing_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.member_id"), nullable=False)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class_schedule.class_id"), nullable=True)
    private_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("private_session.session_id"),
        nullable=True,
        unique=True,
    )
    trainer_id: Mapped[int | None] = mapped_column(
        ForeignKey("trainer.trainer_id"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    member = relationship("Member")
    class_schedule = relationship("ClassSchedule")
    private_session = relationship("PrivateSession")
    trainer = relationship("Trainer")
