# scripts/seed_demo_data.py

import os
import sys
from datetime import datetime, timedelta, time

from sqlalchemy import select

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

# Now imports will work
from models import member, scheduling  # ensure models register
from models.base import get_session
from models.member import Member
from models.scheduling import (
    Trainer,
    Room,
    ClassSchedule,
    TrainerAvailability,
    PrivateSession,
)
from models.payment import Payment, BillingItem
from app.calendar_window import get_booking_now
from app.pricing import DEFAULT_PRIVATE_SESSION_PRICE


def _ensure_room(session, *, name: str, capacity: int) -> Room:
    room = session.scalar(select(Room).where(Room.name == name))
    if room:
        return room
    room = Room(name=name, capacity=capacity)
    session.add(room)
    session.commit()
    session.refresh(room)
    return room


def _ensure_trainer(session, *, first_name: str, last_name: str, email: str) -> Trainer:
    trainer = session.scalar(select(Trainer).where(Trainer.email == email))
    if trainer:
        return trainer
    trainer = Trainer(
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    session.add(trainer)
    session.commit()
    session.refresh(trainer)
    return trainer


def _ensure_member(session, *, first_name: str, last_name: str, email: str) -> Member:
    member_obj = session.scalar(select(Member).where(Member.email == email))
    if member_obj:
        return member_obj
    member_obj = Member(
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    session.add(member_obj)
    session.commit()
    session.refresh(member_obj)
    return member_obj


def _ensure_availability(session, *, trainer: Trainer, windows):
    for day, start_at, end_at in windows:
        exists = session.scalar(
            select(TrainerAvailability).where(
                TrainerAvailability.trainer_id == trainer.trainer_id,
                TrainerAvailability.day_of_week == day,
                TrainerAvailability.start_time == start_at,
                TrainerAvailability.end_time == end_at,
            )
        )
        if exists:
            continue
        session.add(
            TrainerAvailability(
                trainer_id=trainer.trainer_id,
                day_of_week=day,
                start_time=start_at,
                end_time=end_at,
            )
        )
    session.commit()


def _ensure_class(
    session,
    *,
    name: str,
    trainer: Trainer,
    room: Room,
    start_time: datetime,
    duration_hours: int,
    capacity: int,
) -> ClassSchedule:
    existing = session.scalar(
        select(ClassSchedule).where(
            ClassSchedule.trainer_id == trainer.trainer_id,
            ClassSchedule.start_time == start_time,
            ClassSchedule.name == name,
        )
    )
    if existing:
        return existing
    cls = ClassSchedule(
        name=name,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=start_time,
        end_time=start_time + timedelta(hours=duration_hours),
        capacity=capacity,
    )
    session.add(cls)
    session.commit()
    session.refresh(cls)
    return cls


def _ensure_private_session(
    session,
    *,
    member_obj: Member,
    trainer: Trainer,
    room: Room,
    start_time: datetime,
    duration_hours: int,
    price: float | None = None,
) -> PrivateSession:
    existing = session.scalar(
        select(PrivateSession).where(
            PrivateSession.member_id == member_obj.member_id,
            PrivateSession.trainer_id == trainer.trainer_id,
            PrivateSession.start_time == start_time,
        )
    )
    if existing:
        return existing
    final_price = DEFAULT_PRIVATE_SESSION_PRICE if price is None else price
    ps = PrivateSession(
        member_id=member_obj.member_id,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=start_time,
        end_time=start_time + timedelta(hours=duration_hours),
        price=final_price,
    )
    session.add(ps)
    session.commit()
    session.refresh(ps)
    return ps


def _ensure_payment_for_private_session(
    session,
    *,
    private_session: PrivateSession,
    description: str | None = None,
    amount: float | None = None,
) -> Payment:
    existing = session.scalar(
        select(Payment).where(Payment.private_session_id == private_session.session_id)
    )
    if existing:
        return existing
    trainer_label = f"Trainer {private_session.trainer_id}"
    if private_session.trainer:
        trainer_label = (
            f"{private_session.trainer.first_name} {private_session.trainer.last_name}"
        )
    desc = description or (
        f"Private session with {trainer_label} on "
        f"{private_session.start_time.strftime('%b %d %I:%M %p')}"
    )
    final_amount = amount if amount is not None else float(
        private_session.price or DEFAULT_PRIVATE_SESSION_PRICE
    )
    payment = Payment(
        member_id=private_session.member_id,
        amount=final_amount,
        description=desc,
        paid_at=private_session.start_time - timedelta(hours=1),
        private_session_id=private_session.session_id,
    )
    session.add(payment)
    session.commit()
    session.refresh(payment)
    return payment


def _ensure_billing_for_private_session(
    session,
    *,
    private_session: PrivateSession,
    status: str = "pending",
) -> BillingItem:
    existing = session.scalar(
        select(BillingItem).where(
            BillingItem.private_session_id == private_session.session_id
        )
    )
    trainer = private_session.trainer
    trainer_name = (
        f"{trainer.first_name} {trainer.last_name}" if trainer else f"Trainer {private_session.trainer_id}"
    )
    desc = f"Private session with {trainer_name} on {private_session.start_time.strftime('%b %d %I:%M %p')}"
    if existing:
        existing.status = status
        existing.description = desc
        existing.amount = float(private_session.price or DEFAULT_PRIVATE_SESSION_PRICE)
        if status == "paid":
            existing.paid_at = private_session.start_time - timedelta(hours=1)
        return existing
    bill = BillingItem(
        member_id=private_session.member_id,
        private_session_id=private_session.session_id,
        amount=float(private_session.price or DEFAULT_PRIVATE_SESSION_PRICE),
        description=desc,
        status=status,
        paid_at=private_session.start_time - timedelta(hours=1) if status == "paid" else None,
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)
    return bill


def run():
    with get_session() as session:
        main_room = _ensure_room(session, name="Main Room", capacity=20)
        studio = _ensure_room(session, name="Studio B", capacity=12)

        tina = _ensure_trainer(
            session,
            first_name="Tina",
            last_name="Trainer",
            email="tina.trainer@example.com",
        )
        riley = _ensure_trainer(
            session,
            first_name="Riley",
            last_name="Coach",
            email="riley.coach@example.com",
        )

        weekday_windows = [
            (day, time(8, 0), time(12, 0))
            for day in range(0, 5)
        ]
        evening_windows = [
            (day, time(16, 0), time(20, 0))
            for day in range(1, 6)
        ]
        _ensure_availability(session, trainer=tina, windows=weekday_windows)
        _ensure_availability(session, trainer=riley, windows=evening_windows)

        # Upcoming demo classes for both trainers anchored to booking week
        now = get_booking_now()
        _ensure_class(
            session,
            name="Demo Yoga",
            trainer=tina,
            room=main_room,
            start_time=(now + timedelta(days=2)).replace(minute=0, second=0, microsecond=0),
            duration_hours=1,
            capacity=15,
        )
        _ensure_class(
            session,
            name="Strength Circuit",
            trainer=riley,
            room=studio,
            start_time=(now + timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0),
            duration_hours=1,
            capacity=10,
        )

        # Members + private sessions for Riley to test cross-trainer booking
        member_alex = _ensure_member(
            session,
            first_name="Alex",
            last_name="Member",
            email="alex.member@example.com",
        )
        member_jamie = _ensure_member(
            session,
            first_name="Jamie",
            last_name="Member",
            email="jamie.member@example.com",
        )
        alex_session = _ensure_private_session(
            session,
            member_obj=member_alex,
            trainer=riley,
            room=studio,
            start_time=(now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0),
            duration_hours=1,
        )
        _ensure_payment_for_private_session(session, private_session=alex_session)
        _ensure_billing_for_private_session(session, private_session=alex_session, status="paid")
        jamie_session = _ensure_private_session(
            session,
            member_obj=member_jamie,
            trainer=tina,
            room=main_room,
            start_time=(now + timedelta(days=4)).replace(hour=9, minute=0, second=0, microsecond=0),
            duration_hours=1,
        )
        _ensure_payment_for_private_session(session, private_session=jamie_session)
        _ensure_billing_for_private_session(session, private_session=jamie_session, status="paid")

        print("Demo data ready:")
        print(f"  Rooms: {[main_room.name, studio.name]}")
        print(f"  Trainers: {tina.first_name}, {riley.first_name}")
        print("  Members created for testing Alex/Jamie")


if __name__ == "__main__":
    run()
