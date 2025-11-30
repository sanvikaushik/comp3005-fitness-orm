from datetime import datetime, timedelta, time

import pytest

from models.scheduling import Trainer, Room, ClassSchedule
from app.member_service import create_member, book_private_session, log_health_metric
from app.trainer_service import (
    set_trainer_availability,
    get_trainer_schedule,
    lookup_trainer_members,
    create_or_update_class, 
)


def _setup_trainer_and_room(session):
    trainer = Trainer(first_name="Tina", last_name="Trainer", email="trainer@example.com")
    room = Room(name="T Room", capacity=10)
    session.add_all([trainer, room])
    session.commit()
    session.refresh(trainer)
    session.refresh(room)
    return trainer, room


def test_set_trainer_availability_no_overlap(session):
    trainer, _ = _setup_trainer_and_room(session)

    # First availability
    a1 = set_trainer_availability(
        session,
        trainer_id=trainer.trainer_id,
        day_of_week=0,
        start=time(9, 0),
        end=time(11, 0),
    )
    assert a1.availability_id is not None

    # Non-overlapping second availability
    a2 = set_trainer_availability(
        session,
        trainer_id=trainer.trainer_id,
        day_of_week=0,
        start=time(11, 0),
        end=time(13, 0),
    )
    assert a2.availability_id is not None


def test_set_trainer_availability_overlap_fails(session):
    trainer, _ = _setup_trainer_and_room(session)

    set_trainer_availability(
        session,
        trainer_id=trainer.trainer_id,
        day_of_week=1,
        start=time(9, 0),
        end=time(11, 0),
    )

    with pytest.raises(ValueError, match="overlaps"):
        set_trainer_availability(
            session,
            trainer_id=trainer.trainer_id,
            day_of_week=1,
            start=time(10, 0),
            end=time(12, 0),
        )


def test_get_trainer_schedule(session):
    trainer, room = _setup_trainer_and_room(session)

    member = create_member(
        session,
        first_name="Alice",
        last_name="Member",
        email="alice.member@example.com",
    )

    now = datetime.utcnow()

    # Upcoming private session
    ps_start = now + timedelta(days=1)
    ps_end = ps_start + timedelta(hours=1)
    ps = book_private_session(
        session,
        member_id=member.member_id,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=ps_start,
        end_time=ps_end,
    )

    # Upcoming class
    cls_start = now + timedelta(days=2)
    cls_end = cls_start + timedelta(hours=1)
    cls = ClassSchedule(
        name="Trainer Yoga",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=cls_start,
        end_time=cls_end,
        capacity=10,
    )
    session.add(cls)
    session.commit()
    session.refresh(cls)

    schedule = get_trainer_schedule(session, trainer.trainer_id, now=now)
    assert schedule["trainer"].trainer_id == trainer.trainer_id
    assert ps in schedule["upcoming_private_sessions"]
    assert cls in schedule["upcoming_classes"]


def test_lookup_trainer_members(session):
    trainer, room = _setup_trainer_and_room(session)

    # Two members, one with metrics
    m1 = create_member(
        session,
        first_name="Bob",
        last_name="Strong",
        email="bob@example.com",
        target_weight=70.0,
        notes="Build strength",
    )
    m2 = create_member(
        session,
        first_name="Charlie",
        last_name="Speed",
        email="charlie@example.com",
    )

    # Health metric for Bob
    log_health_metric(session, m1.member_id, weight=71.0, heart_rate=65)

    now = datetime.utcnow()
    # PT session with Bob
    ps_start = now + timedelta(days=1)
    ps_end = ps_start + timedelta(hours=1)
    book_private_session(
        session,
        member_id=m1.member_id,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=ps_start,
        end_time=ps_end,
    )

    # Class with Charlie, taught by trainer
    cls_start = now + timedelta(days=2)
    cls_end = cls_start + timedelta(hours=1)
    cls = ClassSchedule(
        name="Speed Class",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=cls_start,
        end_time=cls_end,
        capacity=10,
    )
    session.add(cls)
    session.commit()
    session.refresh(cls)

    from models.scheduling import ClassRegistration

    reg = ClassRegistration(
        member_id=m2.member_id,
        class_id=cls.class_id,
        attended=False,
    )
    session.add(reg)
    session.commit()

    # Lookup by name (case-insensitive)
    results = lookup_trainer_members(
        session,
        trainer_id=trainer.trainer_id,
        name_query="bOb",  # should match Bob
    )

    assert len(results) == 1
    info = results[0]
    assert info["member"].member_id == m1.member_id
    assert info["target_weight"] == 70.0
    assert info["latest_metric"] is not None

def test_create_class_no_conflicts(session):
    trainer, room = _setup_trainer_and_room(session)
    now = datetime.utcnow()

    # First class
    start1 = now + timedelta(days=1)
    end1 = start1 + timedelta(hours=1)
    cls1 = create_or_update_class(
        session,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        name="Morning Yoga",
        capacity=15,
        start_time=start1,
        end_time=end1,
    )
    assert cls1.class_id is not None
    assert cls1.name == "Morning Yoga"

    # Second non-overlapping class (later in the day)
    start2 = end1 + timedelta(hours=1)
    end2 = start2 + timedelta(hours=1)
    cls2 = create_or_update_class(
        session,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        name="Evening Yoga",
        capacity=20,
        start_time=start2,
        end_time=end2,
    )
    assert cls2.class_id is not None
    assert cls2.name == "Evening Yoga"


def test_create_class_with_conflict_fails(session):
    trainer, room = _setup_trainer_and_room(session)
    now = datetime.utcnow()

    # Existing class
    start1 = now + timedelta(days=1)
    end1 = start1 + timedelta(hours=1)
    create_or_update_class(
        session,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        name="Spin Class",
        capacity=10,
        start_time=start1,
        end_time=end1,
    )

    # Overlapping class in same room & trainer should fail
    start2 = start1 + timedelta(minutes=30)
    end2 = start2 + timedelta(hours=1)

    with pytest.raises(ValueError, match="not available"):
        create_or_update_class(
            session,
            trainer_id=trainer.trainer_id,
            room_id=room.room_id,
            name="Overlap Class",
            capacity=12,
            start_time=start2,
            end_time=end2,
        )
