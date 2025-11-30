from datetime import datetime, timedelta

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


def _setup_trainer_room_and_class(session):
    trainer = Trainer(first_name="Tina", last_name="Trainer", email="tina@example.com")
    room = Room(name="Room A", capacity=10)
    session.add_all([trainer, room])
    session.commit()
    session.refresh(trainer)
    session.refresh(room)
    return trainer, room


def test_book_private_session_no_conflicts(session):
    member = create_member(
        session,
        first_name="Carl",
        last_name="Member",
        email="carl@example.com",
    )
    trainer, room = _setup_trainer_room_and_class(session)

    start = datetime.utcnow() + timedelta(days=1, hours=1)
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


def test_group_class_registration_with_capacity(session):
    member = create_member(
        session,
        first_name="Dana",
        last_name="Member",
        email="dana@example.com",
    )
    trainer, room = _setup_trainer_room_and_class(session)

    start = datetime.utcnow() + timedelta(days=1)
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
    from pytest import raises

    with raises(ValueError, match="Class is full"):
        register_for_class(session, member_id=member2.member_id, class_id=cls.class_id)

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
    private_start = now + timedelta(days=1)
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
