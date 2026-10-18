from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class StockMovement(Base):
    """История всех движений товара (продажа, возврат, корректировка)"""
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)

    # Тип движения
    movement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # sale / return / adjustment / import / initial

    quantity_change: Mapped[int] = mapped_column(Integer, nullable=False)  # + или -
    stock_before: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_after: Mapped[int] = mapped_column(Integer, nullable=False)

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    variant: Mapped["ProductVariant"] = relationship("ProductVariant", back_populates="movements")
