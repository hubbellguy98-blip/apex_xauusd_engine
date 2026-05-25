"""Database model mixins."""

from datetime import datetime
import uuid

try:
    from sqlalchemy import DateTime, Uuid
    from sqlalchemy.orm import Mapped, mapped_column
except Exception:
    Mapped = object  # type: ignore

    def mapped_column(*args, **kwargs):  # type: ignore
        return None

    DateTime = Uuid = None  # type: ignore

from src.shared.time_utils import TimeProvider


class AuditMixin:
    created_at: "Mapped[datetime]" = mapped_column(DateTime(timezone=True), default=TimeProvider.get_utc_now, nullable=False)
    updated_at: "Mapped[datetime]" = mapped_column(DateTime(timezone=True), default=TimeProvider.get_utc_now, onupdate=TimeProvider.get_utc_now, nullable=False)


class UUIDPrimaryKeyMixin:
    id: "Mapped[uuid.UUID]" = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
