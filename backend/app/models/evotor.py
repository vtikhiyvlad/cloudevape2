from datetime import datetime
from sqlalchemy import (
    String, Integer, DateTime, JSON, Text, ForeignKey,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class EvotorProductMapping(Base):
    """Связь товара Эвотора с вариантом CloudVape для конкретной точки."""
    __tablename__ = "evotor_product_mappings"
    __table_args__ = (
        UniqueConstraint(
            "evotor_store_id", "evotor_product_id",
            name="uq_evotor_mapping_external_product",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evotor_store_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cloudvape_store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evotor_product_id: Mapped[str] = mapped_column(String(200), nullable=False)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvotorDocument(Base):
    """Импортированный документ Эвотора; уникальность защищает от повторного списания."""
    __tablename__ = "evotor_documents"
    __table_args__ = (
        UniqueConstraint(
            "evotor_store_id", "external_id",
            name="uq_evotor_document_per_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evotor_store_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cloudvape_store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    document_type: Mapped[str] = mapped_column(String(30), nullable=False)
    document_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False, index=True
    )  # pending / processed / needs_mapping / error
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvotorSyncState(Base):
    """Контрольная точка синхронизации по каждой точке Эвотора."""
    __tablename__ = "evotor_sync_states"

    evotor_store_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    cloudvape_store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    last_document_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
