import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, JSONType, ProvenanceMixin, TimestampMixin


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


class ProjectService(IdMixin, TimestampMixin, Base):
    """The project's unified service list (Phase 6): one row per distinct service, merged from every source.

    Rebuilt from `services` after each audit; user choices (selected, renamed, added, kind) are kept.
    """

    __tablename__ = "project_services"
    __table_args__ = (UniqueConstraint("project_id", "normalized_name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="service")  # service | customer_type | generic
    selected: Mapped[bool] = mapped_column(Boolean, default=False)  # a core service: used for keywords
    user_added: Mapped[bool] = mapped_column(Boolean, default=False)
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)  # keep the user's name/kind/selection
    score: Mapped[float] = mapped_column(Float, default=0.0)
    review_mentions: Mapped[int] = mapped_column(Integer, default=0)
    # [{"source", "name", "source_url", "confidence"}]
    sources: Mapped[list] = mapped_column(JSONType, default=list)
