import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, ProvenanceMixin, TimestampMixin


class Service(IdMixin, TimestampMixin, ProvenanceMixin, Base):
    """One service from one source (brief Step 4). The same service from two sources is two rows."""

    __tablename__ = "services"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="CASCADE")
    )
    service_name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
