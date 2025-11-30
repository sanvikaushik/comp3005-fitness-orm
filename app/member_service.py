from datetime import datetime
from typing import Optional, Iterable

from sqlalchemy import select, func, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from models.base import get_session
from models.member import Member, HealthMetric
from models.scheduling import (
    PrivateSession,
    Trainer,
    Room,
    ClassSchedule,
    ClassRegistration,
)


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
    member = Member(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone_number=phone_number,
        date_of_birth=date_of_birth,
        gender=gender,
        target_weight=target_weight,
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
        if key in allowed_fields:
            setattr(member, key, value)

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError("Email or phone already exists")
    session.refresh(member)
    return member


# 2. Log and View Health Metrics
def log_health_metric(
    session: Session,
    member_id: int,
    *,
    weight: Optional[float] = None,
    height: Optional[float] = None,
    heart_rate: Optional[float] = None,
    body_fat_pct: Optional[float] = None,
) -> HealthMetric:
    if not session.get(Member, member_id):
        raise ValueError("Member not found")

    metric = HealthMetric(
        member_id=member_id,
        weight=weight,
        height=height,
        heart_rate=heart_rate,
        body_fat_pct=body_fat_pct,
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


# 3. Book or Reschedule a Private Session
def book_private_session(
    session: Session,
    *,
    member_id: int,
    trainer_id: int,
    room_id: int,
    start_time: datetime,
    end_time: datetime,
) -> PrivateSession:
    if start_time >= end_time:
        raise ValueError("start_time must be before end_time")

    # Basic existence checks
    if not session.get(Member, member_id):
        raise ValueError("Member not found")
    if not session.get(Trainer, trainer_id):
        raise ValueError("Trainer not found")
    if not session.get(Room, room_id):
        raise ValueError("Room not found")

    # Check for conflicts:
    #  - room conflicts with other private sessions or classes
    #  - trainer conflicts with other private sessions or classes
    # (Group needs at least some conflict logic per spec.)

    # Room conflicts with sessions
    room_session_stmt = select(PrivateSession).where(
        PrivateSession.room_id == room_id,
        or_(
            and_(PrivateSession.start_time <= start_time, PrivateSession.end_time > start_time),
            and_(PrivateSession.start_time < end_time, PrivateSession.end_time >= end_time),
            and_(PrivateSession.start_time >= start_time, PrivateSession.end_time <= end_time),
        ),
    )
    if session.scalars(room_session_stmt).first():
        raise ValueError("Room is already booked for another private session in that time")

    # Room conflicts with classes
    room_class_stmt = select(ClassSchedule).where(
        ClassSchedule.room_id == room_id,
        or_(
            and_(ClassSchedule.start_time <= start_time, ClassSchedule.end_time > start_time),
            and_(ClassSchedule.start_time < end_time, ClassSchedule.end_time >= end_time),
            and_(ClassSchedule.start_time >= start_time, ClassSchedule.end_time <= end_time),
        ),
    )
    if session.scalars(room_class_stmt).first():
        raise ValueError("Room is already booked for a class in that time")

    # Trainer conflicts with other private sessions
    trainer_session_stmt = select(PrivateSession).where(
        PrivateSession.trainer_id == trainer_id,
        or_(
            and_(PrivateSession.start_time <= start_time, PrivateSession.end_time > start_time),
            and_(PrivateSession.start_time < end_time, PrivateSession.end_time >= end_time),
            and_(PrivateSession.start_time >= start_time, PrivateSession.end_time <= end_time),
        ),
    )
    if session.scalars(trainer_session_stmt).first():
        raise ValueError("Trainer is already booked for another private session in that time")

    # Trainer conflicts with classes
    trainer_class_stmt = select(ClassSchedule).where(
        ClassSchedule.trainer_id == trainer_id,
        or_(
            and_(ClassSchedule.start_time <= start_time, ClassSchedule.end_time > start_time),
            and_(ClassSchedule.start_time < end_time, ClassSchedule.end_time >= end_time),
            and_(ClassSchedule.start_time >= start_time, ClassSchedule.end_time <= end_time),
        ),
    )
    if session.scalars(trainer_class_stmt).first():
        raise ValueError("Trainer is already teaching a class in that time")

    private = PrivateSession(
        member_id=member_id,
        trainer_id=trainer_id,
        room_id=room_id,
        start_time=start_time,
        end_time=end_time,
    )
    session.add(private)
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

    # Check conflicts exactly like in booking, but ignore the current session itself
    def overlaps_with_other_session(q):
        for s in session.scalars(q):
            if s.session_id != private.session_id:
                return True
        return False

    # Room conflicts
    room_session_stmt = select(PrivateSession).where(
        PrivateSession.room_id == room_id,
        or_(
            and_(PrivateSession.start_time <= start_time, PrivateSession.end_time > start_time),
            and_(PrivateSession.start_time < end_time, PrivateSession.end_time >= end_time),
            and_(PrivateSession.start_time >= start_time, PrivateSession.end_time <= end_time),
        ),
    )
    if overlaps_with_other_session(room_session_stmt):
        raise ValueError("Room is already booked for another private session in that time")

    room_class_stmt = select(ClassSchedule).where(
        ClassSchedule.room_id == room_id,
        or_(
            and_(ClassSchedule.start_time <= start_time, ClassSchedule.end_time > start_time),
            and_(ClassSchedule.start_time < end_time, ClassSchedule.end_time >= end_time),
            and_(ClassSchedule.start_time >= start_time, ClassSchedule.end_time <= end_time),
        ),
    )
    if session.scalars(room_class_stmt).first():
        raise ValueError("Room is already booked for a class in that time")

    # Trainer conflicts (other private sessions)
    trainer_session_stmt = select(PrivateSession).where(
        PrivateSession.trainer_id == private.trainer_id,
        or_(
            and_(PrivateSession.start_time <= start_time, PrivateSession.end_time > start_time),
            and_(PrivateSession.start_time < end_time, PrivateSession.end_time >= end_time),
            and_(PrivateSession.start_time >= start_time, PrivateSession.end_time <= end_time),
        ),
    )
    if overlaps_with_other_session(trainer_session_stmt):
        raise ValueError("Trainer is already booked for another session in that time")

    trainer_class_stmt = select(ClassSchedule).where(
        ClassSchedule.trainer_id == private.trainer_id,
        or_(
            and_(ClassSchedule.start_time <= start_time, ClassSchedule.end_time > start_time),
            and_(ClassSchedule.start_time < end_time, ClassSchedule.end_time >= end_time),
            and_(ClassSchedule.start_time >= start_time, ClassSchedule.end_time <= end_time),
        ),
    )
    if session.scalars(trainer_class_stmt).first():
        raise ValueError("Trainer is already teaching a class in that time")

    private.room_id = room_id
    private.start_time = start_time
    private.end_time = end_time

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
    stmt = select(ClassSchedule).where(ClassSchedule.start_time >= now).order_by(
        ClassSchedule.start_time
    )
    return list(session.scalars(stmt))


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
    session.commit()
    session.refresh(reg)
    return reg

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
      - Profile and active goals (e.g., target weight)
      - Latest health stats (timestamped metric)
      - Count of past classes
      - Upcoming private training sessions
      - Upcoming registered group classes

    Demonstrates ORM relationships, aggregation, and eager loading.
    """
    if now is None:
        now = datetime.utcnow()

    # Eager-load registrations + private sessions for this member
    member_stmt = (
        select(Member)
        .options(
            joinedload(Member.class_registrations)
            .joinedload(ClassRegistration.class_schedule),
            joinedload(Member.private_sessions),
        )
        .where(Member.member_id == member_id)
    )
    member = session.scalars(member_stmt).first()
    if not member:
        raise ValueError("Member not found")

    # Latest health metric (timestamped)
    latest_metric_stmt = (
        select(HealthMetric)
        .where(HealthMetric.member_id == member_id)
        .order_by(HealthMetric.timestamp.desc())
        .limit(1)
    )
    latest_metric = session.scalars(latest_metric_stmt).first()

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

    return {
        "profile": {
            "member": member,
            "target_weight": member.target_weight,
            "notes": member.notes,
        },
        "latest_health_metric": latest_metric,
        "stats": {
            "past_classes_attended": past_classes_count,
            "upcoming_private_session_count": len(upcoming_private_sessions),
            "upcoming_class_count": len(upcoming_classes),
        },
        "upcoming_private_sessions": upcoming_private_sessions,
        "upcoming_classes": upcoming_classes,
    }