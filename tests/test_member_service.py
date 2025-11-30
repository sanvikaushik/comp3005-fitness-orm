from datetime import datetime, timedelta, time

import pytest
from sqlalchemy import select

from app.member_service import (
    create_member,
    update_member,
    log_health_metric,
    get_health_history,
    book_private_session,
    reschedule_private_session,
    list_upcoming_classes,
    register_for_class,
    get_member_dashboard,
)
from models.scheduling import Trainer, Room, ClassSchedule, ClassRegistration
from models.payment import BillingItem
from tests.helpers import add_trainer_availability


def test_create_and_update_member(session):
    m = create_member(
        session,
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
    )
    assert m.member_id is not None

    m2 = update_member(session, m.member_id, phone_number="1234567890")
    assert m2.phone_number == "1234567890"


def test_log_and_view_health_metrics(session):
    m = create_member(
        session,
        first_name="Bob",
        last_name="Jones",
        email="bob@example.com",
    )
    log_health_metric(session, m.member_id, weight=70.0, heart_rate=60)
    log_health_metric(session, m.member_id, weight=69.5, heart_rate=58)

    history = get_health_history(session, m.member_id)
    assert len(history) == 2
    # most recent first
    assert history[0].weight == 69.5


def _setup_trainer_room_and_class(session, availability_windows=None):
    trainer = Trainer(first_name="Tina", last_name="Trainer", email="tina@example.com")
    room = Room(name="Room A", capacity=10)
    session.add_all([trainer, room])
    session.commit()
    session.refresh(trainer)
    session.refresh(room)
    if availability_windows is None:
        add_trainer_availability(session, trainer)
    else:
        add_trainer_availability(session, trainer, windows=availability_windows)
    return trainer, room


def test_book_private_session_no_conflicts(session):
    member = create_member(
        session,
        first_name="Carl",
        last_name="Member",
        email="carl@example.com",
    )
    trainer, room = _setup_trainer_room_and_class(session)

    start = (datetime.utcnow() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)

    private = book_private_session(
        session,
        member_id=member.member_id,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=start,
        end_time=end,
    )

    assert private.session_id is not None


def test_book_private_session_conflicts_with_member_class(session):
    member = create_member(
        session,
        first_name="Gina",
        last_name="Member",
        email="gina@example.com",
    )
    trainer, room = _setup_trainer_room_and_class(session)

    start = (datetime.utcnow() + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)

    cls = ClassSchedule(
        name="Pilates",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=start,
        end_time=end,
        capacity=10,
    )
    session.add(cls)
    session.commit()
    session.refresh(cls)

    session.add(ClassRegistration(member_id=member.member_id, class_id=cls.class_id))
    session.commit()

    with pytest.raises(ValueError, match="class"):
        book_private_session(
            session,
            member_id=member.member_id,
            trainer_id=trainer.trainer_id,
            room_id=room.room_id,
            start_time=start,
            end_time=end,
        )


def test_book_private_session_conflicts_with_member_other_session(session):
    member = create_member(
        session,
        first_name="Helen",
        last_name="Member",
        email="helen@example.com",
    )
    trainer1, room1 = _setup_trainer_room_and_class(session)
    trainer2 = Trainer(first_name="Nina", last_name="Coach", email="nina.coach@example.com")
    room2 = Room(name="Room B", capacity=8)
    session.add_all([trainer2, room2])
    session.commit()
    session.refresh(trainer2)
    session.refresh(room2)
    add_trainer_availability(session, trainer2)

    start = (datetime.utcnow() + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)

    book_private_session(
        session,
        member_id=member.member_id,
        trainer_id=trainer1.trainer_id,
        room_id=room1.room_id,
        start_time=start,
        end_time=end,
    )

    with pytest.raises(ValueError, match="private session"):
        book_private_session(
            session,
            member_id=member.member_id,
            trainer_id=trainer2.trainer_id,
            room_id=room2.room_id,
            start_time=start,
            end_time=end,
        )


def test_group_class_registration_with_capacity(session):
    member = create_member(
        session,
        first_name="Dana",
        last_name="Member",
        email="dana@example.com",
    )
    trainer, room = _setup_trainer_room_and_class(session)

    start = (datetime.utcnow() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)

    # create one class
    cls = ClassSchedule(
        name="Yoga",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=start,
        end_time=end,
        capacity=1,
    )
    session.add(cls)
    session.commit()
    session.refresh(cls)

    upcoming = list_upcoming_classes(session)
    assert len(upcoming) == 1

    reg = register_for_class(session, member_id=member.member_id, class_id=cls.class_id)
    assert reg.registration_id is not None

    # second registration should fail due to capacity
    member2 = create_member(
        session,
        first_name="Eva",
        last_name="Member",
        email="eva@example.com",
    )

    with pytest.raises(ValueError, match="Class is full"):
        register_for_class(session, member_id=member2.member_id, class_id=cls.class_id)


def test_book_private_session_rejects_unavailable_window(session):
    member = create_member(
        session,
        first_name="Unavailable",
        last_name="Member",
        email="unavailable@example.com",
    )
    desired_start = (datetime.utcnow() + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    desired_end = desired_start + timedelta(hours=1)
    limited_windows = [
        (desired_start.weekday(), time(6, 0), time(7, 0)),
    ]
    trainer, room = _setup_trainer_room_and_class(session, availability_windows=limited_windows)

    with pytest.raises(ValueError, match="available"):
        book_private_session(
            session,
            member_id=member.member_id,
            trainer_id=trainer.trainer_id,
            room_id=room.room_id,
            start_time=desired_start,
            end_time=desired_end,
        )


def test_register_for_class_requires_trainer_availability(session):
    member = create_member(
        session,
        first_name="Classy",
        last_name="Member",
        email="classy@example.com",
    )
    base_start = (datetime.utcnow() + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    window_day = base_start.weekday()
    windows = [
        (window_day, time(9, 0), time(11, 0)),
    ]
    trainer, room = _setup_trainer_room_and_class(session, availability_windows=windows)

    inside_start = base_start.replace(hour=9)
    inside_end = inside_start + timedelta(hours=1)
    cls_ok = ClassSchedule(
        name="Morning Sculpt",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=inside_start,
        end_time=inside_end,
        capacity=5,
    )
    session.add(cls_ok)
    session.commit()
    session.refresh(cls_ok)

    outside_start = base_start.replace(hour=13)
    outside_end = outside_start + timedelta(hours=1)
    cls_bad = ClassSchedule(
        name="Afternoon Spin",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=outside_start,
        end_time=outside_end,
        capacity=5,
    )
    session.add(cls_bad)
    session.commit()
    session.refresh(cls_bad)

    reg = register_for_class(session, member_id=member.member_id, class_id=cls_ok.class_id)
    assert reg.registration_id is not None

    with pytest.raises(ValueError, match="available"):
        register_for_class(session, member_id=member.member_id, class_id=cls_bad.class_id)


def test_register_for_class_creates_billing_item(session):
    member = create_member(
        session,
        first_name="Bill",
        last_name="Able",
        email="bill@example.com",
    )
    trainer, room = _setup_trainer_room_and_class(session)

    start = (datetime.utcnow() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    cls = ClassSchedule(
        name="Strength",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=start,
        end_time=end,
        capacity=10,
        price=75,
    )
    session.add(cls)
    session.commit()
    session.refresh(cls)

    register_for_class(session, member_id=member.member_id, class_id=cls.class_id)

    bill = session.scalar(
        select(BillingItem).where(
            BillingItem.member_id == member.member_id,
            BillingItem.class_id == cls.class_id,
        )
    )
    assert bill is not None
    assert float(bill.amount) == 75
    assert bill.status == "pending"

def test_member_dashboard(session):
    # Setup: create member, trainer, room
    member = create_member(
        session,
        first_name="Dash",
        last_name="Board",
        email="dash@example.com",
        target_weight=62.0,
        notes="Cut to 62kg by summer.",
    )

    trainer = Trainer(first_name="Tina", last_name="Trainer", email="dash-trainer@example.com")
    room = Room(name="Dashboard Room", capacity=10)
    session.add_all([trainer, room])
    session.commit()
    session.refresh(trainer)
    session.refresh(room)
    add_trainer_availability(session, trainer)

    now = datetime.utcnow()

    # Health metrics: two entries, latest should be picked
    earlier_metric_time = now - timedelta(days=2)
    later_metric_time = now - timedelta(days=1)

    # Manually set timestamps via log_health_metric + direct update
    m1 = log_health_metric(session, member.member_id, weight=65.0, heart_rate=72)
    m1.timestamp = earlier_metric_time

    m2 = log_health_metric(session, member.member_id, weight=64.0, heart_rate=70)
    m2.timestamp = later_metric_time

    session.commit()

    # Past class (already ended, attended == True)
    past_start = now - timedelta(days=7)
    past_end = past_start + timedelta(hours=1)
    past_class = ClassSchedule(
        name="Past Yoga",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=past_start,
        end_time=past_end,
        capacity=20,
    )
    session.add(past_class)
    session.commit()
    session.refresh(past_class)

    past_reg = ClassRegistration(
        member_id=member.member_id,
        class_id=past_class.class_id,
        attended=True,
    )
    session.add(past_reg)
    session.commit()

    # Upcoming class (registered, future)
    future_start = now + timedelta(days=3)
    future_end = future_start + timedelta(hours=1)
    future_class = ClassSchedule(
        name="Future HIIT",
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=future_start,
        end_time=future_end,
        capacity=20,
    )
    session.add(future_class)
    session.commit()
    session.refresh(future_class)

    future_reg = ClassRegistration(
        member_id=member.member_id,
        class_id=future_class.class_id,
        attended=False,
    )
    session.add(future_reg)
    session.commit()

    # Upcoming private session
    private_start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    private_end = private_start + timedelta(hours=1)
    ps = book_private_session(
        session,
        member_id=member.member_id,
        trainer_id=trainer.trainer_id,
        room_id=room.room_id,
        start_time=private_start,
        end_time=private_end,
    )

    # Call dashboard
    dashboard = get_member_dashboard(session, member.member_id, now=now)

    # Basic structure checks
    assert "profile" in dashboard
    assert "latest_health_metric" in dashboard
    assert "stats" in dashboard
    assert "upcoming_private_sessions" in dashboard
    assert "upcoming_classes" in dashboard

    # Profile / goals
    assert dashboard["profile"]["member"].member_id == member.member_id
    assert dashboard["profile"]["target_weight"] == 62.0
    assert "Cut to 62kg" in dashboard["profile"]["notes"]

    # Latest metric should be the later one (64.0)
    latest = dashboard["latest_health_metric"]
    assert latest is not None
    assert latest.weight == 64.0

    # Past classes count (only one attended, in the past)
    assert dashboard["stats"]["past_classes_attended"] == 1

    # Upcoming sessions and classes
    assert dashboard["stats"]["upcoming_private_session_count"] == 1
    assert dashboard["stats"]["upcoming_class_count"] == 1

    assert ps in dashboard["upcoming_private_sessions"]
    assert future_class in dashboard["upcoming_classes"]
