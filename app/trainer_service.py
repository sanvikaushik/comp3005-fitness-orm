from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import Session, joinedload

from models.scheduling import (
    Trainer,
    TrainerAvailability,
    PrivateSession,
    ClassSchedule,
    ClassRegistration,
    Room,
)
from models.member import Member, HealthMetric
from models.payment import BillingItem, Payment
from models.equipment import EquipmentIssue, Equipment
from models.base import get_session


# ---------------------------
# 1. Set Availability
# ---------------------------

def set_trainer_availability(
    session: Session,
    *,
    trainer_id: int,
    day_of_week: int,
    start: time,
    end: time,
) -> TrainerAvailability:
    """
    Define a new availability window for a trainer on a specific day.
    Prevents overlapping windows for the same trainer + day_of_week.
    """
    if start >= end:
        raise ValueError("Availability start time must be before end time")
    if start.minute != 0 or start.second != 0 or end.minute != 0 or end.second != 0:
        raise ValueError("Availability times must start/end on the hour (e.g., 09:00)")

    trainer = session.get(Trainer, trainer_id)
    if not trainer:
        raise ValueError("Trainer not found")

    # Check for overlapping availability for this trainer/day
    overlap_stmt = select(TrainerAvailability).where(
        TrainerAvailability.trainer_id == trainer_id,
        TrainerAvailability.day_of_week == day_of_week,
        or_(
            and_(
                TrainerAvailability.start_time <= start,
                TrainerAvailability.end_time > start,
            ),
            and_(
                TrainerAvailability.start_time < end,
                TrainerAvailability.end_time >= end,
            ),
            and_(
                TrainerAvailability.start_time >= start,
                TrainerAvailability.end_time <= end,
            ),
        ),
    )
    if session.scalars(overlap_stmt).first():
        raise ValueError("Availability window overlaps with an existing one")

    avail = TrainerAvailability(
        trainer_id=trainer_id,
        day_of_week=day_of_week,
        start_time=start,
        end_time=end,
    )
    session.add(avail)
    session.commit()
    session.refresh(avail)
    return avail


def update_trainer_availability(
    session: Session,
    *,
    availability_id: int,
    start: time,
    end: time,
) -> TrainerAvailability:
    """
    Update start/end of an existing availability window while keeping trainer/day fixed.
    Prevents overlaps with other windows on the same day.
    """
    if start >= end:
        raise ValueError("Availability start time must be before end time")
    if start.minute != 0 or start.second != 0 or end.minute != 0 or end.second != 0:
        raise ValueError("Availability times must start/end on the hour (e.g., 09:00)")

    availability = session.get(TrainerAvailability, availability_id)
    if not availability:
        raise ValueError("Availability window not found")

    overlap_stmt = select(TrainerAvailability).where(
        TrainerAvailability.trainer_id == availability.trainer_id,
        TrainerAvailability.day_of_week == availability.day_of_week,
        TrainerAvailability.availability_id != availability.availability_id,
        or_(
            and_(
                TrainerAvailability.start_time <= start,
                TrainerAvailability.end_time > start,
            ),
            and_(
                TrainerAvailability.start_time < end,
                TrainerAvailability.end_time >= end,
            ),
            and_(
                TrainerAvailability.start_time >= start,
                TrainerAvailability.end_time <= end,
            ),
        ),
    )
    if session.scalars(overlap_stmt).first():
        raise ValueError("Updated window overlaps with an existing one")

    availability.start_time = start
    availability.end_time = end
    session.commit()
    session.refresh(availability)
    return availability


def _trainer_supports_window(
    session: Session,
    trainer_id: int,
    start_time: datetime,
    end_time: datetime,
) -> bool:
    day = start_time.weekday()
    start_t = start_time.time()
    end_t = end_time.time()
    avail_stmt = select(TrainerAvailability).where(
        TrainerAvailability.trainer_id == trainer_id,
        TrainerAvailability.day_of_week == day,
    )
    return any(
        av.start_time <= start_t and av.end_time >= end_t
        for av in session.scalars(avail_stmt)
    )


# ---------------------------
# 2. Schedule View
# ---------------------------

def get_trainer_schedule(
    session: Session,
    trainer_id: int,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """
    Returns upcoming PT sessions and group classes for a trainer.

    Demonstrates eager loading of related entities:
      - PrivateSession.member
      - ClassSchedule.room
    """
    if now is None:
        now = datetime.utcnow()

    trainer_stmt = (
        select(Trainer)
        .options(
            joinedload(Trainer.private_sessions)
            .joinedload(PrivateSession.member),
            joinedload(Trainer.private_sessions)
            .joinedload(PrivateSession.room),
            joinedload(Trainer.classes)
            .joinedload(ClassSchedule.room),
            joinedload(Trainer.classes)
            .selectinload(ClassSchedule.registrations),
            joinedload(Trainer.availabilities),
            joinedload(Trainer.primary_rooms),
        )
        .where(Trainer.trainer_id == trainer_id)
    )
    trainer = session.scalars(trainer_stmt).first()
    if not trainer:
        raise ValueError("Trainer not found")

    upcoming_private = [
        ps
        for ps in trainer.private_sessions
        if ps.start_time >= now
    ]

    upcoming_classes = [
        cls
        for cls in trainer.classes
        if cls.start_time >= now
    ]

    billing_stmt = (
        select(BillingItem)
        .options(
            joinedload(BillingItem.member),
            joinedload(BillingItem.class_schedule).joinedload(ClassSchedule.room),
            joinedload(BillingItem.trainer),
        )
        .where(
            or_(
                BillingItem.trainer_id == trainer_id,
                BillingItem.class_schedule.has(ClassSchedule.trainer_id == trainer_id),
            )
        )
        .order_by(BillingItem.created_at.desc())
    )
    billing_items = list(session.scalars(billing_stmt).unique())

    pt_payment_stmt = (
        select(Payment)
        .options(
            joinedload(Payment.member),
            joinedload(Payment.private_session)
            .joinedload(PrivateSession.room),
        )
        .join(PrivateSession, Payment.private_session_id == PrivateSession.session_id)
        .where(PrivateSession.trainer_id == trainer_id)
        .order_by(Payment.paid_at.desc())
    )
    private_session_payments = list(session.scalars(pt_payment_stmt))

    trainer_room_ids = {room.room_id for room in trainer.primary_rooms}

    equipment_issues_stmt = (
        select(EquipmentIssue)
        .options(
            joinedload(EquipmentIssue.equipment),
            joinedload(EquipmentIssue.room),
        )
        .join(Equipment, Equipment.equipment_id == EquipmentIssue.equipment_id, isouter=True)
        .where(
            or_(
                EquipmentIssue.room_id.in_(trainer_room_ids) if trainer_room_ids else False,
                Equipment.trainer_id == trainer_id,
            )
        )
        .order_by(EquipmentIssue.reported_at.desc())
    )
    equipment_issues = list(session.execute(equipment_issues_stmt).unique().scalars())

    equipment_inventory_stmt = (
        select(Equipment)
        .options(
            joinedload(Equipment.room),
            joinedload(Equipment.trainer),
        )
        .where(
            or_(
                Equipment.room_id.in_(trainer_room_ids) if trainer_room_ids else False,
                Equipment.trainer_id == trainer_id,
            )
        )
        .order_by(Equipment.name)
    )
    equipment_inventory = list(session.scalars(equipment_inventory_stmt))

    return {
        "trainer": trainer,
        "upcoming_private_sessions": sorted(
            upcoming_private, key=lambda ps: ps.start_time
        ),
        "upcoming_classes": sorted(
            upcoming_classes, key=lambda c: c.start_time
        ),
        "billing_items": billing_items,
        "private_session_payments": private_session_payments,
        "equipment_issues": equipment_issues,
        "equipment_inventory": equipment_inventory,
    }


# ---------------------------
# 3. Member Lookup
# ---------------------------

def lookup_trainer_members(
    session: Session,
    *,
    trainer_id: int,
    name_query: str,
) -> list[dict]:
    """
    Search members that this trainer interacts with (PT sessions or classes)
    by (case-insensitive) name and show:
      - Member basic info
      - Current goal (target_weight, notes)
      - Last health metric (if any)

    No editing is performed – read-only lookup.
    """
    # Collect member_ids from PT sessions
    pt_stmt = select(PrivateSession.member_id).where(
        PrivateSession.trainer_id == trainer_id
    )
    pt_member_ids = {mid for (mid,) in session.execute(pt_stmt).all()}

    # Collect member_ids from classes taught by this trainer
    class_stmt = (
        select(ClassRegistration.member_id)
        .join(ClassSchedule, ClassRegistration.class_id == ClassSchedule.class_id)
        .where(ClassSchedule.trainer_id == trainer_id)
    )
    class_member_ids = {mid for (mid,) in session.execute(class_stmt).all()}

    all_member_ids = pt_member_ids | class_member_ids
    if not all_member_ids:
        return []

    # Case-insensitive name search using eager loading of health metrics
    q = name_query.lower()
    members_stmt = (
        select(Member)
        .options(joinedload(Member.health_metrics))
        .where(
            Member.member_id.in_(all_member_ids),
            or_(
                func.lower(Member.first_name).like(f"%{q}%"),
                func.lower(Member.last_name).like(f"%{q}%"),
            ),
        )
    )

    # ✅ use unique() + scalars() when eager-loading collections
    members = list(session.execute(members_stmt).unique().scalars())

    results: list[dict] = []
    for m in members:
        latest_metric: Optional[HealthMetric] = None
        if m.health_metrics:
            latest_metric = max(m.health_metrics, key=lambda hm: hm.timestamp)

        results.append(
            {
                "member": m,
                "target_weight": m.target_weight,
                "notes": m.notes,
                "latest_metric": latest_metric,
            }
        )

    return results

def create_or_update_class(
    session: Session,
    *,
    trainer_id: int,
    room_id: int,
    name: str,
    capacity: int,
    start_time: datetime,
    end_time: datetime,
    price: float,
    class_id: Optional[int] = None,
) -> ClassSchedule:
    """
    Create a new group class or update an existing one.

    Enforces:
      - start_time < end_time
      - trainer exists
      - room exists
      - no overlapping classes or private sessions for the same room
      - no overlapping classes or private sessions for the same trainer
    """
    start_time = start_time.replace(minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(hours=1)
    if price <= 0:
        raise ValueError("Class price must be positive")

    trainer = session.get(Trainer, trainer_id)
    if not trainer:
        raise ValueError("Trainer not found")

    room = session.get(Room, room_id)
    if not room:
        raise ValueError("Room not found")

    # If updating, fetch existing class and make sure trainer matches
    existing_class: Optional[ClassSchedule] = None
    if class_id is not None:
        existing_class = session.get(ClassSchedule, class_id)
        if not existing_class:
            raise ValueError("Class not found")
        if existing_class.trainer_id != trainer_id:
            raise ValueError("Trainer is not allowed to modify this class")

    if not _trainer_supports_window(session, trainer_id, start_time, end_time):
        raise ValueError("Trainer does not have availability for this time window")

    # helper for time overlap: [start_time, end_time) intersects with [col_start, col_end)
    def _overlap_filter(col_start, col_end):
        return and_(col_start < end_time, col_end > start_time)

    # --- Room conflicts: other classes ---
    room_class_conflict_stmt = select(ClassSchedule).where(
        ClassSchedule.room_id == room_id,
        _overlap_filter(ClassSchedule.start_time, ClassSchedule.end_time),
    )
    if existing_class is not None:
        room_class_conflict_stmt = room_class_conflict_stmt.where(
            ClassSchedule.class_id != existing_class.class_id
        )

    if session.scalars(room_class_conflict_stmt).first():
        raise ValueError("Room is not available for this time slot")

    # --- Room conflicts: private sessions ---
    room_session_conflict_stmt = select(PrivateSession).where(
        PrivateSession.room_id == room_id,
        _overlap_filter(PrivateSession.start_time, PrivateSession.end_time),
    )
    if session.scalars(room_session_conflict_stmt).first():
        raise ValueError("Room is not available for this time slot")

    # --- Trainer conflicts: other classes ---
    trainer_class_conflict_stmt = select(ClassSchedule).where(
        ClassSchedule.trainer_id == trainer_id,
        _overlap_filter(ClassSchedule.start_time, ClassSchedule.end_time),
    )
    if existing_class is not None:
        trainer_class_conflict_stmt = trainer_class_conflict_stmt.where(
            ClassSchedule.class_id != existing_class.class_id
        )

    if session.scalars(trainer_class_conflict_stmt).first():
        raise ValueError("Trainer is not available for this time slot")

    # --- Trainer conflicts: private sessions ---
    trainer_session_conflict_stmt = select(PrivateSession).where(
        PrivateSession.trainer_id == trainer_id,
        _overlap_filter(PrivateSession.start_time, PrivateSession.end_time),
    )
    if session.scalars(trainer_session_conflict_stmt).first():
        raise ValueError("Trainer is not available for this time slot")

    # Create or update the class
    if existing_class is None:
        cls = ClassSchedule(
            name=name,
            trainer_id=trainer_id,
            room_id=room_id,
            start_time=start_time,
            end_time=end_time,
            capacity=capacity,
            price=price,
        )
        session.add(cls)
    else:
        cls = existing_class
        cls.name = name
        cls.room_id = room_id
        cls.start_time = start_time
        cls.end_time = end_time
        cls.capacity = capacity
        cls.price = price

    session.commit()
    session.refresh(cls)
    return cls
