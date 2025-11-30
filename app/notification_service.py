from __future__ import annotations

from sqlalchemy.orm import Session

from models.notification import Notification


def add_member_notification(session: Session, member_id: int, message: str) -> Notification:
    note = Notification(member_id=member_id, message=message)
    session.add(note)
    return note


def add_trainer_notification(session: Session, trainer_id: int, message: str) -> Notification:
    note = Notification(trainer_id=trainer_id, message=message)
    session.add(note)
    return note


def mark_notifications_read(session: Session, notifications: list[Notification]) -> None:
    if not notifications:
        return
    for note in notifications:
        note.is_read = True
    session.flush()
