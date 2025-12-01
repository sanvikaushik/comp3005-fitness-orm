from datetime import datetime
from typing import Optional, Iterable

from sqlalchemy import select, func, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from models.member import HealthMetric

from models.base import get_session
from models.member import Member, HealthMetric
from models.scheduling import (
    PrivateSession,
    Trainer,
    Room,
    ClassSchedule,
    ClassRegistration,
    TrainerAvailability,
)
from models.payment import BillingItem
from app.pricing import DEFAULT_PRIVATE_SESSION_PRICE

MAX_TARGET_WEIGHT_KG = 300.0


def _normalize_target_weight(
    value: Optional[float | str],
    *,
    current: Optional[float] = None,
) -> Optional[float]:
    """
    Ensure target weight is numeric and within the allowed range.
    Accepts floats or numeric strings; blank strings become None.
    """
    if value is None:
        return current
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return current
        try:
            value = float(stripped)
        except ValueError:
            raise ValueError("Target weight must be a valid number.")
    if value < 0:
        raise ValueError("Target weight must be a positive number.")
    if value > MAX_TARGET_WEIGHT_KG:
        return current
    return value


# 1. Create or Update Member Profile
def create_member(
    session: Session,
    *,
    first_name: str,
    last_name: str,
    email: str,
    phone_number: Optional[str] = None,
    date_of_birth: Optional[datetime.date] = None,
    gender: Optional[str] = None,
    target_weight: Optional[float] = None,
    notes: Optional[str] = None,
) -> Member:
    normalized_target = _normalize_target_weight(target_weight)

    member = Member(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone_number=phone_number,
        date_of_birth=date_of_birth,
        gender=gender,
        target_weight=normalized_target,
        notes=notes,
    )
    session.add(member)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError("Email or phone already exists")
    session.refresh(member)
    return member


def update_member(
    session: Session,
    member_id: int,
    **changes,
) -> Member:
    member = session.get(Member, member_id)
    if not member:
        raise ValueError("Member not found")

    allowed_fields = {
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "date_of_birth",
        "gender",
        "target_weight",
        "notes",
    }
    for key, value in changes.items():
        if key not in allowed_fields:
            continue
        if key == "target_weight":
            normalized = _normalize_target_weight(value, current=member.target_weight)
            setattr(member, key, normalized)
        else:
            setattr(member, key, value)

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError("Email or phone already exists")
    session.refresh(member)
    return member


# 2. Log and View Health Metrics
from models.member import HealthMetric

def log_health_metric(
    session,
    member_id: int,
    weight: float | None = None,
    heart_rate: int | None = None,
) -> HealthMetric:
    metric = HealthMetric(
        member_id=member_id,
        weight=weight,
        heart_rate=heart_rate,
        # timestamp is filled automatically by default=datetime.utcnow
    )
    session.add(metric)
    session.commit()
    session.refresh(metric)
    return metric


def get_health_history(
    session: Session,
    member_id: int,
) -> list[HealthMetric]:
    stmt = (
        select(HealthMetric)
        .where(HealthMetric.member_id == member_id)
        .order_by(HealthMetric.timestamp.desc())
    )
    return list(session.scalars(stmt))


# helpers for time overlap (used by booking/rescheduling)
def _time_overlaps(start: datetime, end: datetime, other_start, other_end) -> bool:
    return not (end <= other_start or start >= other_end)


def _ensure_trainer_availability(
    session: Session,
    *,
    trainer_id: int,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """
    Ensure the requested window falls within one of the trainer's availability slots.
    Raises ValueError with a descriptive message if not allowed.
    """
    day_of_week = start_time.weekday()
    start_t = start_time.time()
    end_t = end_time.time()

    avail_stmt = select(TrainerAvailability).where(
        TrainerAvailability.trainer_id == trainer_id,
        TrainerAvailability.day_of_week == day_of_week,
    )
    availabilities = list(session.scalars(avail_stmt))

    if not availabilities:
        raise ValueError("Trainer has no availability on this day.")

    within_window = any(
        start_t >= av.start_time and end_t <= av.end_time
        for av in availabilities
    )
    if not within_window:
        raise ValueError("Requested time is outside the trainer's available hours.")


def _format_private_payment_description(
    trainer: Trainer | None,
    start_time: datetime,
) -> str:
    if trainer:
        trainer_name = f"{trainer.first_name} {trainer.last_name}"
    else:
        trainer_name = "trainer"
    return f"Private session with {trainer_name} on {start_time.strftime('%b %d %I:%M %p')}"


def _ensure_private_billing(
    session: Session,
    private_session: PrivateSession,
    *,
    trainer: Trainer | None,
) -> BillingItem:
    desc = _format_private_payment_description(trainer, private_session.start_time)
    bill = session.scalar(
        select(BillingItem).where(
            BillingItem.private_session_id == private_session.session_id
        )
    )
    if bill:
        bill.description = desc
        bill.amount = float(private_session.price or 0)
        bill.updated_at = datetime.utcnow()
        if trainer:
            bill.trainer_id = trainer.trainer_id
        return bill
    bill = BillingItem(
        member_id=private_session.member_id,
        private_session_id=private_session.session_id,
        trainer_id=trainer.trainer_id if trainer else None,
        amount=float(private_session.price or 0),
        description=desc,
        status="pending",
    )
    session.add(bill)
    return bill


# 3. Book or Reschedule a Private Session
def book_private_session(
    session: Session,
    *,
    member_id: int,
    trainer_id: int,
    room_id: int,
    start_time: datetime,
    end_time: datetime,
    price: float | None = None,
) -> PrivateSession:
    if start_time >= end_time:
        raise ValueError("start_time must be before end_time")

    # Basic existence checks
    member = session.get(Member, member_id)
    if not member:
        raise ValueError("Member not found")
    trainer = session.get(Trainer, trainer_id)
    if not trainer:
        raise ValueError("Trainer not found")
    if not session.get(Room, room_id):
        raise ValueError("Room not found")
    
        # Trainer must actually be available at this time (per TrainerAvailability)
    _ensure_trainer_availability(
        session,
        trainer_id=trainer_id,
        start_time=start_time,
        end_time=end_time,
    )

    # Check for conflicts:
    #  - room conflicts with other private sessions or classes
    #  - trainer conflicts with other private sessions or classes
    # (Group needs at least some conflict logic per spec.)

    overlap_ps = and_(PrivateSession.start_time < end_time, PrivateSession.end_time > start_time)
    overlap_cls = and_(ClassSchedule.start_time < end_time, ClassSchedule.end_time > start_time)

    # Room conflicts with sessions
    room_session_stmt = select(PrivateSession).where(
        PrivateSession.room_id == room_id,
        overlap_ps,
    )
    if session.scalars(room_session_stmt).first():
        raise ValueError("Room is already booked for another private session in that time")

    # Room conflicts with classes
    room_class_stmt = select(ClassSchedule).where(
        ClassSchedule.room_id == room_id,
        overlap_cls,
    )
    if session.scalars(room_class_stmt).first():
        raise ValueError("Room is already booked for a class in that time")

    # Trainer conflicts with other private sessions
    trainer_session_stmt = select(PrivateSession).where(
        PrivateSession.trainer_id == trainer_id,
        overlap_ps,
    )
    if session.scalars(trainer_session_stmt).first():
        raise ValueError("Trainer is already booked for another private session in that time")

    # Trainer conflicts with classes
    trainer_class_stmt = select(ClassSchedule).where(
        ClassSchedule.trainer_id == trainer_id,
        overlap_cls,
    )
    if session.scalars(trainer_class_stmt).first():
        raise ValueError("Trainer is already teaching a class in that time")

    # Member conflicts (other private sessions)
    member_session_stmt = select(PrivateSession).where(
        PrivateSession.member_id == member_id,
        overlap_ps,
    )
    if session.scalars(member_session_stmt).first():
        raise ValueError("Member already has a private session in that time")

    # Member conflicts with registered classes
    member_class_stmt = (
        select(ClassSchedule)
        .join(ClassRegistration, ClassRegistration.class_id == ClassSchedule.class_id)
        .where(
            ClassRegistration.member_id == member_id,
            overlap_cls,
        )
    )
    if session.scalars(member_class_stmt).first():
        raise ValueError("Member is registered for a class in that time")

    final_price = DEFAULT_PRIVATE_SESSION_PRICE if price is None else price

    private = PrivateSession(
        member_id=member_id,
        trainer_id=trainer_id,
        room_id=room_id,
        start_time=start_time,
        end_time=end_time,
        price=final_price,
    )
    session.add(private)
    session.flush()
    _ensure_private_billing(session, private, trainer=trainer)
    session.commit()
    session.refresh(private)
    return private


def reschedule_private_session(
    session: Session,
    *,
    session_id: int,
    new_room_id: Optional[int] = None,
    new_start: Optional[datetime] = None,
    new_end: Optional[datetime] = None,
) -> PrivateSession:
    private = session.get(PrivateSession, session_id)
    if not private:
        raise ValueError("Private session not found")

    room_id = new_room_id or private.room_id
    start_time = new_start or private.start_time
    end_time = new_end or private.end_time

    if start_time >= end_time:
        raise ValueError("start_time must be before end_time")

    _ensure_trainer_availability(
        session,
        trainer_id=private.trainer_id,
        start_time=start_time,
        end_time=end_time,
    )

    # Check conflicts exactly like in booking, but ignore the current session itself
    def overlaps_with_other_session(q):
        for s in session.scalars(q):
            if s.session_id != private.session_id:
                return True
        return False

    # Room conflicts
    overlap_ps = and_(PrivateSession.start_time < end_time, PrivateSession.end_time > start_time)
    overlap_cls = and_(ClassSchedule.start_time < end_time, ClassSchedule.end_time > start_time)

    room_session_stmt = select(PrivateSession).where(
        PrivateSession.room_id == room_id,
        overlap_ps,
    )
    if overlaps_with_other_session(room_session_stmt):
        raise ValueError("Room is already booked for another private session in that time")

    room_class_stmt = select(ClassSchedule).where(
        ClassSchedule.room_id == room_id,
        overlap_cls,
    )
    if session.scalars(room_class_stmt).first():
        raise ValueError("Room is already booked for a class in that time")

    # Trainer conflicts (other private sessions)
    trainer_session_stmt = select(PrivateSession).where(
        PrivateSession.trainer_id == private.trainer_id,
        overlap_ps,
    )
    if overlaps_with_other_session(trainer_session_stmt):
        raise ValueError("Trainer is already booked for another session in that time")

    trainer_class_stmt = select(ClassSchedule).where(
        ClassSchedule.trainer_id == private.trainer_id,
        overlap_cls,
    )
    if session.scalars(trainer_class_stmt).first():
        raise ValueError("Trainer is already teaching a class in that time")

    member_session_stmt = select(PrivateSession).where(
        PrivateSession.member_id == private.member_id,
        PrivateSession.session_id != private.session_id,
        overlap_ps,
    )
    if session.scalars(member_session_stmt).first():
        raise ValueError("Member already has a private session in that time")

    member_class_stmt = (
        select(ClassSchedule)
        .join(ClassRegistration, ClassRegistration.class_id == ClassSchedule.class_id)
        .where(
            ClassRegistration.member_id == private.member_id,
            overlap_cls,
        )
    )
    if session.scalars(member_class_stmt).first():
        raise ValueError("Member is registered for a class in that time")

    private.room_id = room_id
    private.start_time = start_time
    private.end_time = end_time

    trainer = private.trainer
    _ensure_private_billing(session, private, trainer=trainer)
    session.commit()
    session.refresh(private)
    return private


# 4. Register for a Group Class
def list_upcoming_classes(
    session: Session,
    now: Optional[datetime] = None,
) -> list[ClassSchedule]:
    if now is None:
        now = datetime.utcnow()
    stmt = (
        select(ClassSchedule)
        .where(ClassSchedule.start_time >= now)
        .options(
            selectinload(ClassSchedule.registrations),
            joinedload(ClassSchedule.trainer),
            joinedload(ClassSchedule.room),
        )
        .order_by(ClassSchedule.start_time)
    )
    classes = list(session.scalars(stmt))
    result: list[ClassSchedule] = []
    for cls in classes:
        if _class_within_trainer_availability(session, cls):
            result.append(cls)
    return result


def _class_within_trainer_availability(
    session: Session,
    cls: ClassSchedule,
) -> bool:
    start_time = cls.start_time
    end_time = cls.end_time
    day = start_time.weekday()
    start_t = start_time.time()
    end_t = end_time.time()
    avail_stmt = select(TrainerAvailability).where(
        TrainerAvailability.trainer_id == cls.trainer_id,
        TrainerAvailability.day_of_week == day,
    )
    availabilities = list(session.scalars(avail_stmt))
    if not availabilities:
        return True
    return any(av.start_time <= start_t and av.end_time >= end_t for av in availabilities)


def register_for_class(
    session: Session,
    *,
    member_id: int,
    class_id: int,
) -> ClassRegistration:
    if not session.get(Member, member_id):
        raise ValueError("Member not found")

    cls = session.get(ClassSchedule, class_id)
    if not cls:
        raise ValueError("Class not found")

    _ensure_trainer_availability(
        session,
        trainer_id=cls.trainer_id,
        start_time=cls.start_time,
        end_time=cls.end_time,
    )

    # Prevent duplicate registration
    exists_stmt = select(ClassRegistration).where(
        ClassRegistration.member_id == member_id,
        ClassRegistration.class_id == class_id,
    )
    if session.scalars(exists_stmt).first():
        raise ValueError("Member already registered for this class")

    # Capacity check
    count_stmt = (
        select(func.count(ClassRegistration.registration_id))
        .where(ClassRegistration.class_id == class_id)
    )
    current_count = session.scalar(count_stmt) or 0

    if current_count >= cls.capacity:
        raise ValueError("Class is full")

    reg = ClassRegistration(member_id=member_id, class_id=class_id)
    session.add(reg)

    bill = _ensure_class_billing(session, member_id, cls)

    session.commit()
    session.refresh(reg)
    if bill:
        session.refresh(bill)
    return reg


def _ensure_class_billing(session: Session, member_id: int, cls: ClassSchedule) -> BillingItem:
    bill_stmt = select(BillingItem).where(
        BillingItem.member_id == member_id,
        BillingItem.class_id == cls.class_id,
        BillingItem.status != "cancelled",
    )
    existing = session.scalars(bill_stmt).first()
    if existing:
        if existing.trainer_id != cls.trainer_id:
            existing.trainer_id = cls.trainer_id
        return existing

    bill = BillingItem(
        member_id=member_id,
        class_id=cls.class_id,
        trainer_id=cls.trainer_id,
        amount=float(cls.price or 0),
        description=f"Class {cls.name} with {cls.trainer.first_name}",
        status="pending",
    )
    session.add(bill)
    return bill

def get_member_with_metrics(session, member_id: int) -> Member | None:
    """
    Eager-load a member and ALL their health metrics in ONE query.
    Demonstrates ORM eager loading using joinedload() for the bonus.
    """
    stmt = (
        select(Member)
        .options(joinedload(Member.health_metrics))  # <- EAGER LOAD
        .where(Member.member_id == member_id)
    )
    return session.scalars(stmt).first()

def get_member_dashboard(
    session: Session,
    member_id: int,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """
    Member Dashboard (rubric-aligned)

    Returns a summary view for a member including:
      - Profile and active goals (e.g., target weight, notes)
      - Latest health stats (timestamped metric)
      - Health history (most recent N entries)
      - Count of past classes
      - Upcoming private training sessions
      - Upcoming registered group classes
    """
    if now is None:
        now = datetime.utcnow()
    
    # Eager-load registrations + private sessions for this member
    member_stmt = (
        select(Member)
        .options(
            joinedload(Member.class_registrations)
            .joinedload(ClassRegistration.class_schedule)
            .joinedload(ClassSchedule.trainer),
            joinedload(Member.class_registrations)
            .joinedload(ClassRegistration.class_schedule)
            .joinedload(ClassSchedule.room),
            joinedload(Member.private_sessions)
            .joinedload(PrivateSession.trainer),
            joinedload(Member.private_sessions)
            .joinedload(PrivateSession.room),
        )
        .where(Member.member_id == member_id)
    )
    member = session.execute(member_stmt).unique().scalars().first()
    if not member:
        raise ValueError("Member not found")

    # Latest health metric (timestamped)
    latest_metric = (
        session.query(HealthMetric)
        .filter(HealthMetric.member_id == member_id)
        .order_by(HealthMetric.timestamp.desc())
        .first()
    )

    # Health history – latest 20 for table
    history_stmt = (
        select(HealthMetric)
        .where(HealthMetric.member_id == member_id)
        .order_by(HealthMetric.timestamp.desc())
        .limit(20)
    )
    health_history = list(session.scalars(history_stmt))

    # Past classes attended (end_time < now and attended == True)
    past_classes_count_stmt = (
        select(func.count(ClassRegistration.registration_id))
        .join(ClassSchedule, ClassRegistration.class_id == ClassSchedule.class_id)
        .where(
            ClassRegistration.member_id == member_id,
            ClassSchedule.end_time < now,
            ClassRegistration.attended.is_(True),
        )
    )
    past_classes_count = session.scalar(past_classes_count_stmt) or 0

    # Upcoming private PT sessions (for this member)
    upcoming_private_sessions = [
        ps
        for ps in member.private_sessions
        if ps.start_time >= now
    ]

    # Upcoming group classes the member is registered for
    upcoming_classes = [
        cr.class_schedule
        for cr in member.class_registrations
        if cr.class_schedule is not None
        and cr.class_schedule.start_time >= now
    ]

    # Health stats / goals
    latest_weight = latest_metric.weight if latest_metric and latest_metric.weight is not None else None
    target_weight = member.target_weight
    weight_delta = None
    if latest_weight is not None and target_weight is not None:
        weight_delta = latest_weight - target_weight  # +ve = above goal, -ve = below

    return {
        "profile": {
            "member": member,
            "target_weight": member.target_weight,
            "notes": member.notes,
        },
        "latest_health_metric": latest_metric,
        "health_history": health_history,
        "stats": {
            "past_classes_attended": past_classes_count,
            "upcoming_private_session_count": len(upcoming_private_sessions),
            "upcoming_class_count": len(upcoming_classes),
            "latest_weight": latest_weight,
            "target_weight": target_weight,
            "weight_delta": weight_delta,
        },
        "upcoming_private_sessions": upcoming_private_sessions,
        "upcoming_classes": upcoming_classes,
        "pt_session_price": DEFAULT_PRIVATE_SESSION_PRICE,
    }
