from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.member import Member
from models.scheduling import PrivateSession, ClassSchedule, Room
from models.equipment import Equipment
from models.payment import Payment

from models.scheduling import (
    Room,
    PrivateSession,
    ClassSchedule,
)
from app.member_service import reschedule_private_session
from app.trainer_service import create_or_update_class

def admin_reassign_session_room(
    session: Session,
    *,
    session_id: int,
    new_room_id: int,
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
        class_id=cls.class_id,
    )
    return updated

def update_equipment_status(session, equipment_id: int, new_status: str) -> Equipment:
    """
    Update the status of an equipment item.

    Used by the Admin Equipment UI.
    """
    eq = session.get(Equipment, equipment_id)
    if not eq:
        raise ValueError(f"Equipment {equipment_id} not found.")

    eq.status = new_status
    session.commit()
    session.refresh(eq)
    return eq

def record_payment(
    session,
    member_id: int,
    amount: float,
    description: str | None = None,
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
    )
    session.add(payment)
    session.commit()
    session.refresh(payment)
    return payment

