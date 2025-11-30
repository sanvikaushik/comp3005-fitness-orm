from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    String,
    Integer,
    Text,
    ForeignKey,
    DateTime,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Equipment(Base):
    __tablename__ = "equipment"

    equipment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="operational")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("room.room_id"), nullable=True)
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey("trainer.trainer_id"), nullable=True)

    room: Mapped["Room | None"] = relationship("Room")
    trainer: Mapped["Trainer | None"] = relationship("Trainer")

    issues: Mapped[list["EquipmentIssue"]] = relationship(
        "EquipmentIssue",
        back_populates="equipment",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Equipment id={self.equipment_id} name={self.name!r} status={self.status!r}>"


class EquipmentIssue(Base):
    __tablename__ = "equipment_issue"

    issue_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment.equipment_id"),
        nullable=True,
    )
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.room_id"),
        nullable=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    reported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    equipment: Mapped["Equipment | None"] = relationship("Equipment", back_populates="issues")
    room: Mapped["Room | None"] = relationship("Room")

    def __repr__(self) -> str:
        return f"<EquipmentIssue id={self.issue_id} status={self.status!r}>"
