from datetime import date, datetime

from sqlalchemy import (
    Integer,
    String,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Text,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Member(Base):
    __tablename__ = "member"

    member_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Optional: fitness goal fields
    target_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    health_metrics: Mapped[list["HealthMetric"]] = relationship(
        back_populates="member", cascade="all, delete-orphan",  order_by="HealthMetric.timestamp.desc()",
    )

    class_registrations: Mapped[list["ClassRegistration"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )

    private_sessions: Mapped[list["PrivateSession"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )
    last_metric_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)



class HealthMetric(Base):
    __tablename__ = "health_metric"

    metric_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    member_id: Mapped[int] = mapped_column(
        ForeignKey("member.member_id"),
        nullable=False,
        # ❗ IMPORTANT: do NOT set unique=True here
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    heart_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    member = relationship("Member", back_populates="health_metrics")