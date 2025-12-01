from datetime import datetime, timedelta

import pytest

from models.scheduling import Trainer, Room, ClassSchedule
from models.equipment import Equipment
from app.member_service import create_member, book_private_session
from app.admin_service import (
    admin_reassign_session_room,
    admin_reschedule_class,
    create_equipment,
    log_equipment_issue,
    update_equipment_issue_status,
    update_equipment_status,
)
from tests.helpers import add_trainer_availability


def _setup_trainer_room_member(session):
    trainer = Trainer(first_name="Tina", last_name="Trainer", email="admin-trainer@example.com")
    room1 = Room(name="Room A", capacity=10)
    room2 = Room(name="Room B", capacity=10)
    member = create_member(
        session,
        first_name="Admin",
        last_name="Member",
        email="admin-member@example.com",
    )
    session.add_all([trainer, room1, room2])
    session.commit()
    session.refresh(trainer)
    session.refresh(room1)
    session.refresh(room2)
    add_trainer_availability(session, trainer)
    return trainer, room1, room2, member

def test_admin_reassign_session_room_no_conflict(session):
    trainer, room1, room2, member = _setup_trainer_room_member(session)
    now = datetime.utcnow()

    # Original session in room1
    start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    ps = book_private_session(
        session,
        member_id=member.member_id,
        trainer_id=trainer.trainer_id,
        room_id=room1.room_id,
        start_time=start,
        end_time=end,
    )

    # Admin moves it to room2
    updated = admin_reassign_session_room(
        session,
        session_id=ps.session_id,
        new_room_id=room2.room_id,
    )

    assert updated.room_id == room2.room_id

def test_admin_reschedule_class_with_conflict_and_success(session):
    trainer, room1, room2, member = _setup_trainer_room_member(session)
    now = datetime.utcnow()

    # Existing class in room1
    start1 = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    end1 = start1 + timedelta(hours=1)
    cls1 = ClassSchedule(
        name="Existing Class",
        trainer_id=trainer.trainer_id,
        room_id=room1.room_id,
        start_time=start1,
        end_time=end1,
        capacity=10,
    )
    session.add(cls1)
    session.commit()
    session.refresh(cls1)

    # Another class we want to move (currently in room2, non-overlapping)
    start2 = end1 + timedelta(hours=1)
    end2 = start2 + timedelta(hours=1)
    cls2 = ClassSchedule(
        name="Movable Class",
        trainer_id=trainer.trainer_id,
        room_id=room2.room_id,
        start_time=start2,
        end_time=end2,
        capacity=10,
    )
    session.add(cls2)
    session.commit()
    session.refresh(cls2)

    # Try to move cls2 into a conflicting time in room1 -> should fail
    conflict_start = start1 + timedelta(minutes=30)
    conflict_end = conflict_start + timedelta(hours=1)

    with pytest.raises(ValueError, match="not available"):
        admin_reschedule_class(
            session,
            class_id=cls2.class_id,
            new_room_id=room1.room_id,
            new_start=conflict_start,
            new_end=conflict_end,
        )

    # Move cls2 to a later, non-conflicting slot in room1 -> should succeed
    ok_start = end1 + timedelta(hours=2)
    ok_end = ok_start + timedelta(hours=1)

    updated = admin_reschedule_class(
        session,
        class_id=cls2.class_id,
        new_room_id=room1.room_id,
        new_start=ok_start,
        new_end=ok_end,
    )

    assert updated.room_id == room1.room_id
    assert updated.start_time == ok_start
    assert updated.end_time == ok_end

def test_equipment_issue_logging(session):
    trainer, room1, room2, member = _setup_trainer_room_member(session)

    equipment = create_equipment(
        session,
        name="Treadmill A",
        status="operational",
        notes="Fresh install",
        room_id=room1.room_id,
        trainer_id=trainer.trainer_id,
    )
    assert isinstance(equipment, Equipment)
    assert equipment.room_id == room1.room_id
    assert equipment.trainer_id == trainer.trainer_id

    issue = log_equipment_issue(
        session,
        equipment_id=equipment.equipment_id,
        room_id=room1.room_id,
        description="Belt slipping",
    )
    assert issue.issue_id is not None
    assert issue.status == "open"

    updated_issue = update_equipment_issue_status(
        session,
        issue_id=issue.issue_id,
        new_status="resolved",
        resolved=True,
    )
    assert updated_issue.status == "resolved"
    assert updated_issue.resolved_at is not None


def test_update_equipment_validates_room_and_trainer(session):
    trainer, room1, room2, member = _setup_trainer_room_member(session)

    equipment = create_equipment(
        session,
        name="Bench Press",
        status="operational",
        notes="",
        room_id=room1.room_id,
        trainer_id=trainer.trainer_id,
    )

    updated = update_equipment_status(
        session,
        equipment_id=equipment.equipment_id,
        new_status="maintenance",
        notes="Pads torn",
        room_id=room2.room_id,
        trainer_id=trainer.trainer_id,
    )
    assert updated.status == "maintenance"
    assert updated.room_id == room2.room_id

    with pytest.raises(ValueError, match="Room"):
        update_equipment_status(
            session,
            equipment_id=equipment.equipment_id,
            new_status="operational",
            room_id=9999,
        )

    with pytest.raises(ValueError, match="Trainer"):
        update_equipment_status(
            session,
            equipment_id=equipment.equipment_id,
            new_status="operational",
            trainer_id=9999,
        )
