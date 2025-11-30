from __future__ import annotations

from datetime import datetime, timedelta, time

from sqlalchemy import select, text

from models.base import Base, engine, get_session
from models.member import Member, HealthMetric
from models.scheduling import (
    Trainer,
    Room,
    PrivateSession,
    ClassSchedule,
    TrainerAvailability,
)
from models.equipment import Equipment, EquipmentIssue
from models.payment import Payment

from app import init_db


def clear_all_data() -> None:
    """Drop and recreate all tables, including triggers/views."""
    with engine.begin() as conn:
        conn.execute(text("DROP VIEW IF EXISTS member_latest_metric_view CASCADE"))
    Base.metadata.drop_all(bind=engine)
    init_db.init_db()


def seed_demo_data() -> None:
    """Seed the database with a consistent demo data set."""
    clear_all_data()
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    with get_session() as session:
        # Members
        member_specs = [
            ("Avery", "Stone", "avery@example.com", 68.0, "Lean muscle focus", 66.0, 60),
            ("Blake", "Summers", "blake@example.com", 82.0, "Prep for race", 80.0, 58),
            ("Casey", "Rivera", "casey@example.com", 74.0, "Improve endurance", 70.0, 62),
            ("Dakota", "Reed", "dakota@example.com", 90.0, "Cut to 85kg", 85.0, 65),
            ("Emery", "Shaw", "emery@example.com", 63.0, "Maintain weight", 63.0, 57),
        ]
        members = []
        metrics: list[HealthMetric] = []
        for first_name, last_name, email, target_weight, note_text, weight, hr in member_specs:
            member = Member(
                first_name=first_name,
                last_name=last_name,
                email=email,
                target_weight=target_weight,
                notes=note_text,
                last_metric_at=now - timedelta(days=1),
            )
            session.add(member)
            session.flush()
            members.append(member)
            metrics.append(
                HealthMetric(
                    member_id=member.member_id,
                    weight=weight,
                    heart_rate=hr,
                    timestamp=now - timedelta(days=1),
                )
            )
        session.add_all(metrics)

        # Trainers
        trainers = [
            Trainer(first_name="Tina", last_name="Ray", email="tina.ray@example.com"),
            Trainer(first_name="Riley", last_name="Cole", email="riley.cole@example.com"),
            Trainer(first_name="Morgan", last_name="Lee", email="morgan.lee@example.com"),
        ]
        session.add_all(trainers)
        session.flush()

        # Rooms (2-3 per trainer)
        room_presets = [
            ("Summit Studio", 18),
            ("Zen Loft", 14),
            ("Cardio Bay", 16),
            ("Strength Lab", 12),
            ("Power Pod", 10),
            ("Mind-Body Room", 20),
        ]
        rooms: list[Room] = []
        preset_index = 0
        for trainer in trainers:
            for _ in range(2):
                base_name, capacity = room_presets[preset_index % len(room_presets)]
                label_suffix = (preset_index // len(room_presets)) + 1
                room = Room(
                    name=f"{base_name} {label_suffix}",
                    capacity=capacity,
                    primary_trainer_id=trainer.trainer_id,
                )
                rooms.append(room)
                preset_index += 1
        session.add_all(rooms)
        session.flush()

        # Trainer availability (Mon-Fri 9-5)
        for trainer in trainers:
            for day in range(5):
                session.add(
                    TrainerAvailability(
                        trainer_id=trainer.trainer_id,
                        day_of_week=day,
                        start_time=time(9, 0),
                        end_time=time(17, 0),
                    )
                )

        session.commit()

        # Private sessions
        next_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        if next_monday <= now:
            next_monday += timedelta(weeks=1)

        private_sessions: list[PrivateSession] = []
        for trainer_index, trainer in enumerate(trainers):
            trainer_rooms = session.scalars(
                select(Room).where(Room.primary_trainer_id == trainer.trainer_id)
            ).all()
            for idx in range(3):
                member = members[(trainer_index + idx) % len(members)]
                day = next_monday + timedelta(days=(trainer_index * 2 + idx) % 5)
                start_time = day.replace(hour=9 + idx, minute=0)
                private_sessions.append(
                    PrivateSession(
                        member_id=member.member_id,
                        trainer_id=trainer.trainer_id,
                        room_id=trainer_rooms[idx % len(trainer_rooms)].room_id,
                        start_time=start_time,
                        end_time=start_time + timedelta(hours=1),
                    )
                )
        # two trainers in different rooms but same window to test conflict guarding
        overlap_start = (next_monday + timedelta(days=6)).replace(hour=15, minute=0)
        for idx, trainer in enumerate(trainers[:2]):
            trainer_rooms = session.scalars(
                select(Room).where(Room.primary_trainer_id == trainer.trainer_id)
            ).all()
            member = members[(idx + 2) % len(members)]
            private_sessions.append(
                PrivateSession(
                    member_id=member.member_id,
                    trainer_id=trainer.trainer_id,
                    room_id=trainer_rooms[0].room_id,
                    start_time=overlap_start,
                    end_time=overlap_start + timedelta(hours=1),
                )
            )
        session.add_all(private_sessions)

        classes: list[ClassSchedule] = []
        class_keys: set[tuple[int, datetime]] = set()
        for trainer_index, trainer in enumerate(trainers):
            trainer_rooms = session.scalars(
                select(Room).where(Room.primary_trainer_id == trainer.trainer_id)
            ).all()
            for idx in range(2):
                day = next_monday + timedelta(days=(trainer_index * 2 + idx + 1) % 5)
                start_time = day.replace(hour=13 + idx, minute=0)
                key = (trainer.trainer_id, start_time)
                if key in class_keys:
                    continue
                class_keys.add(key)
                classes.append(
                    ClassSchedule(
                        name=f"{trainer.first_name} Studio {idx + 1}",
                        trainer_id=trainer.trainer_id,
                        room_id=trainer_rooms[idx % len(trainer_rooms)].room_id,
                        start_time=start_time,
                        end_time=start_time + timedelta(hours=1),
                        capacity=12 + idx * 3,
                        price=40 + idx * 5,
                    )
                )
        session.add_all(classes)

        # Equipment and issues
        equipment_items = [
            Equipment(
                name="Treadmill A",
                status="operational",
                notes="Fresh belt",
                room_id=rooms[0].room_id,
                trainer_id=rooms[0].primary_trainer_id,
            ),
            Equipment(
                name="Rowing Machine B",
                status="maintenance",
                notes="Awaiting parts",
                room_id=rooms[1].room_id,
                trainer_id=rooms[1].primary_trainer_id,
            ),
            Equipment(
                name="Bench Press C",
                status="operational",
                notes="New padding",
                room_id=rooms[2].room_id,
                trainer_id=rooms[2].primary_trainer_id,
            ),
            Equipment(
                name="Spin Bike D",
                status="maintenance",
                notes="Chain noise",
                room_id=rooms[3].room_id,
                trainer_id=rooms[3].primary_trainer_id,
            ),
            Equipment(
                name="Elliptical E",
                status="operational",
                notes="Lubed joints",
                room_id=rooms[4].room_id,
                trainer_id=rooms[4].primary_trainer_id,
            ),
        ]
        session.add_all(equipment_items)
        session.flush()

        issues = [
            EquipmentIssue(
                equipment_id=equipment_items[1].equipment_id,
                room_id=rooms[0].room_id,
                description="Resistance cable fraying",
                status="in_progress",
            ),
            EquipmentIssue(
                equipment_id=equipment_items[3].equipment_id,
                room_id=rooms[2].room_id,
                description="Pedal assembly loose",
                status="open",
            ),
        ]
        session.add_all(issues)

        # Sample payments
        for member in members[:3]:
            session.add(
                Payment(
                    member_id=member.member_id,
                    amount=120.0,
                    description="Monthly membership",
                    paid_at=now - timedelta(days=member.member_id),
                )
            )

        session.commit()
