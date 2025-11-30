from datetime import datetime, time

from sqlalchemy import (
    Integer,
    String,
    DateTime,
    Time,
    ForeignKey,
    Boolean,
    Numeric,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Trainer(Base):
    __tablename__ = "trainer"

    trainer_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    private_sessions: Mapped[list["PrivateSession"]] = relationship(
        back_populates="trainer",
        cascade="all, delete-orphan",
    )
    classes: Mapped[list["ClassSchedule"]] = relationship(
        back_populates="trainer",
        cascade="all, delete-orphan",
    )

    availabilities: Mapped[list["TrainerAvailability"]] = relationship(
        "TrainerAvailability",
        back_populates="trainer",
        cascade="all, delete-orphan",
    )

class Room(Base):
    __tablename__ = "room"

    room_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_trainer_id: Mapped[int | None] = mapped_column(ForeignKey("trainer.trainer_id"), nullable=True)

    private_sessions: Mapped[list["PrivateSession"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )
    classes: Mapped[list["ClassSchedule"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )
    primary_trainer: Mapped["Trainer | None"] = relationship(
        "Trainer",
        backref="primary_rooms",
        foreign_keys=[primary_trainer_id],
    )


class PrivateSession(Base):
    __tablename__ = "private_session"

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.member_id"), nullable=False)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainer.trainer_id"), nullable=False)
    room_id: Mapped[int] = mapped_column(ForeignKey("room.room_id"), nullable=False)

    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    member: Mapped["Member"] = relationship(back_populates="private_sessions")
    trainer: Mapped["Trainer"] = relationship(back_populates="private_sessions")
    room: Mapped["Room"] = relationship(back_populates="private_sessions")


class ClassSchedule(Base):
    __tablename__ = "class_schedule"

    class_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainer.trainer_id"), nullable=False)
    room_id: Mapped[int] = mapped_column(ForeignKey("room.room_id"), nullable=False)

    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=50)

    trainer: Mapped["Trainer"] = relationship(back_populates="classes")
    room: Mapped["Room"] = relationship(back_populates="classes")
    registrations: Mapped[list["ClassRegistration"]] = relationship(
        back_populates="class_schedule", cascade="all, delete-orphan"
    )


class ClassRegistration(Base):
    __tablename__ = "class_registration"

    registration_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.member_id"), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("class_schedule.class_id"), nullable=False)

    attended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    member: Mapped["Member"] = relationship(back_populates="class_registrations")
    class_schedule: Mapped["ClassSchedule"] = relationship(back_populates="registrations")

class TrainerAvailability(Base):
    """
    Trainer availability window for a specific day of week.
    day_of_week: 0 = Monday ... 6 = Sunday
    """
    __tablename__ = "trainer_availability"

    availability_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainer.trainer_id"), nullable=False)

    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0–6
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    trainer: Mapped["Trainer"] = relationship(
        "Trainer",
        back_populates="availabilities",
    )
