from __future__ import annotations

from datetime import datetime, timedelta

# Anchors all booking/demo schedules to the same consistent week so the UI
# always shows "Week of Dec 1" regardless of the actual current date.
BOOKING_WEEK_START = datetime(2025, 12, 1, 0, 0)


def get_booking_now() -> datetime:
    """Return the fake 'current' time used for all booking widgets."""
    return BOOKING_WEEK_START


def get_booking_horizon(weeks: int = 1) -> datetime:
    """Return the exclusive end datetime for the booking window."""
    return BOOKING_WEEK_START + timedelta(weeks=weeks)
