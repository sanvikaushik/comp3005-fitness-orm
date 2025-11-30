# scripts/seed_admin_data.py

import os
import sys

# --- Ensure project root is on PYTHONPATH ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

# Import BOTH model modules so all relationships resolve
from models import member, scheduling  # noqa: F401

from models.base import get_session
from models.scheduling import Room


def run():
    with get_session() as session:
        room = Room(
            name="Secondary Room",
            capacity=15,
        )
        session.add(room)
        session.commit()
        session.refresh(room)
        print("Created Room ID:", room.room_id)


if __name__ == "__main__":
    run()
