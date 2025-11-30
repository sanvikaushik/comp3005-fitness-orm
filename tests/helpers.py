from __future__ import annotations

from datetime import time
from typing import Iterable, Tuple

from models.scheduling import Trainer, TrainerAvailability


AvailabilityWindow = Tuple[int, time, time]


def add_trainer_availability(
    session,
    trainer: Trainer,
    *,
    windows: Iterable[AvailabilityWindow] | None = None,
    start_hour: int = 6,
    end_hour: int = 21,
) -> list[TrainerAvailability]:
    """
    Ensure a trainer has availability windows persisted for upcoming checks.

    By default this seeds every day of week with a wide-open window so tests
    can create private sessions and classes without thinking about scheduling.
    Custom windows can be supplied to model specific availability constraints.
    """
    trainer_id = trainer.trainer_id
    if trainer_id is None:
        raise ValueError("Trainer must be persisted before adding availability")

    if windows is None:
        windows = [
            (day, time(start_hour, 0), time(end_hour, 0))
            for day in range(7)
        ]

    availabilities = [
        TrainerAvailability(
            trainer_id=trainer_id,
            day_of_week=day,
            start_time=start,
            end_time=end,
        )
        for day, start, end in windows
    ]
    session.add_all(availabilities)
    session.commit()
    return availabilities
