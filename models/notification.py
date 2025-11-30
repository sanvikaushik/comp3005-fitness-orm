from datetime import datetime

from sqlalchemy import Integer, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Notification(Base):
    __tablename__ = "notification"

    notification_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    member_id: Mapped[int | None] = mapped_column(
        ForeignKey("member.member_id"), nullable=True
    )
    trainer_id: Mapped[int | None] = mapped_column(
        ForeignKey("trainer.trainer_id"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
