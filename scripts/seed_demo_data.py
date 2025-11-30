# scripts/seed_demo_data.py

import os
import sys
from datetime import datetime, timedelta

# --- Ensure project root is on PYTHONPATH ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

# Now imports will work
from models import member, scheduling  # ensure models register
from models.base import get_session
from models.scheduling import Trainer, Room, ClassSchedule


def run():
    with get_session() as session:
        # 1) Create Trainer
        trainer = Trainer(
            first_name="Tina",
            last_name="Trainer",
            email="tina.trainer@example.com",
        )

        # 2) Create Room
        room = Room(
            name="Main Room",
            capacity=20,
        )

        session.add_all([trainer, room])
        session.commit()
        session.refresh(trainer)
        session.refresh(room)

        print("Created Trainer ID:", trainer.trainer_id)
        print("Created Room ID:", room.room_id)

        # 3) Create one upcoming class for UI testing
        start = datetime.utcnow() + timedelta(days=2)
        end = start + timedelta(hours=1)

        cls = ClassSchedule(
            name="Demo Yoga",
            trainer_id=trainer.trainer_id,
            room_id=room.room_id,
            start_time=start,
            end_time=end,
            capacity=15,
        )

        session.add(cls)
        session.commit()
        session.refresh(cls)

        print("Created Class ID:", cls.class_id)


if __name__ == "__main__":
    run()
