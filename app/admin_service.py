from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.member import Member
from models.scheduling import PrivateSession, ClassSchedule, Room
from datetime import datetime

from models.equipment import Equipment, EquipmentIssue
from models.payment import Payment

from models.scheduling import (
    Room,
    PrivateSession,
    ClassSchedule,
    Trainer,
)
from app.member_service import reschedule_private_session
from app.trainer_service import create_or_update_class

def admin_reassign_session_room(
    session: Session,
    *,
    session_id: int,
    new_room_id: int,
    new_start: datetime | None = None,
    new_end: datetime | None = None,
) -> PrivateSession:
    """
    Admin operation: Room Booking for PT sessions.

    Reassigns the room for an existing private session while
    reusing the same conflict checks as the member-side
    rescheduling logic (room + trainer availability).
    """
    session_obj = session.get(PrivateSession, session_id)
    if not session_obj:
        raise ValueError("Private session not found")

    # Ensure the new room exists
    room = session.get(Room, new_room_id)
    if not room:
        raise ValueError("Room not found")

    # Delegate to existing reschedule logic which already
    # checks for overlaps with classes and other sessions.
    updated = reschedule_private_session(
        session,
        session_id=session_id,
        new_room_id=new_room_id,
        new_start=new_start,
        new_end=new_end,
    )
    return updated

def admin_reschedule_class(
    session: Session,
    *,
    class_id: int,
    new_room_id: int,
    new_start: datetime,
    new_end: datetime,
) -> ClassSchedule:
    """
    Admin operation: Class Management.

    Reschedules an existing class by changing its room and/or time.
    Uses the same conflict rules as trainer-side create_or_update_class:
      - start < end
      - trainer & room must be free (no overlapping sessions/classes)
    """
    cls = session.get(ClassSchedule, class_id)
    if not cls:
        raise ValueError("Class not found")

    # Ensure the new room exists
    room = session.get(Room, new_room_id)
    if not room:
        raise ValueError("Room not found")

    # Delegate to trainer logic, using the existing trainer_id
    updated = create_or_update_class(
        session,
        trainer_id=cls.trainer_id,
        room_id=new_room_id,
        name=cls.name,
        capacity=cls.capacity,
        start_time=new_start,
        end_time=new_end,
        price=float(cls.price or 0),
        class_id=cls.class_id,
    )
    return updated

def update_equipment_status(
    session,
    equipment_id: int,
    new_status: str,
    notes: str | None = None,
    room_id: int | None = None,
    trainer_id: int | None = None,
) -> Equipment:
    """
    Update the status of an equipment item.

    Used by the Admin Equipment UI.
    """
    eq = session.get(Equipment, equipment_id)
    if not eq:
        raise ValueError(f"Equipment {equipment_id} not found.")

    eq.status = new_status
    if notes is not None:
        eq.notes = notes

    if room_id is not None:
        if room_id:
            room = session.get(Room, room_id)
            if not room:
                raise ValueError(f"Room {room_id} not found.")
            eq.room_id = room.room_id
        else:
            eq.room_id = None

    if trainer_id is not None:
        if trainer_id:
            trainer = session.get(Trainer, trainer_id)
            if not trainer:
                raise ValueError(f"Trainer {trainer_id} not found.")
            eq.trainer_id = trainer.trainer_id
        else:
            eq.trainer_id = None
    session.commit()
    session.refresh(eq)
    return eq


def create_equipment(
    session,
    *,
    name: str,
    status: str = "operational",
    notes: str | None = None,
    room_id: int | None = None,
    trainer_id: int | None = None,
) -> Equipment:
    equipment = Equipment(
        name=name,
        status=status,
        notes=notes,
        room_id=room_id,
        trainer_id=trainer_id,
    )
    session.add(equipment)
    session.commit()
    session.refresh(equipment)
    return equipment


def log_equipment_issue(
    session,
    *,
    equipment_id: int | None,
    room_id: int | None,
    description: str,
    status: str = "open",
) -> EquipmentIssue:
    if equipment_id is not None:
        equipment = session.get(Equipment, equipment_id)
        if not equipment:
            raise ValueError(f"Equipment {equipment_id} not found.")

    if room_id is not None:
        room = session.get(Room, room_id)
        if not room:
            raise ValueError(f"Room {room_id} not found.")

    issue = EquipmentIssue(
        equipment_id=equipment_id,
        room_id=room_id,
        description=description,
        status=status,
    )
    session.add(issue)
    session.commit()
    session.refresh(issue)
    return issue


def update_equipment_issue_status(
    session,
    *,
    issue_id: int,
    new_status: str,
    resolved: bool = False,
) -> EquipmentIssue:
    issue = session.get(EquipmentIssue, issue_id)
    if not issue:
        raise ValueError(f"Issue {issue_id} not found.")

    issue.status = new_status
    if resolved:
        issue.resolved_at = datetime.utcnow()

    session.commit()
    session.refresh(issue)
    return issue

def record_payment(
    session,
    member_id: int,
    amount: float,
    description: str | None = None,
    private_session_id: int | None = None,
) -> Payment:
    """
    Create a payment row for a member.

    This is used by the Admin Payments UI, and will also trigger any
    DB trigger you've defined on the payment table.
    """
    member = session.get(Member, member_id)
    if not member:
        raise ValueError(f"Member {member_id} not found.")

    payment = Payment(
        member_id=member_id,
        amount=amount,
        description=description,
        paid_at=datetime.utcnow(),
        private_session_id=private_session_id,
    )
    session.add(payment)
    session.commit()
    session.refresh(payment)
    return payment
