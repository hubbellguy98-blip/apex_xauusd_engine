"""SQLAlchemy declarative base."""

try:
    from sqlalchemy.orm import DeclarativeBase
except Exception:
    class DeclarativeBase:  # type: ignore[no-redef]
        metadata = None


class Base(DeclarativeBase):
    pass
