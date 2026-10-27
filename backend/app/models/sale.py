from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    items_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Оплата
    payment_type: Mapped[str] = mapped_column(String(20), default="cash")  # cash / card / combo
    payment_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    payment_card: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    items: Mapped[list["SaleItem"]] = relationship(
        "SaleItem", back_populates="sale", cascade="all, delete-orphan"
    )


class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    variant_info: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_at_sale: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    returned_quantity: Mapped[int] = mapped_column(Integer, default=0)

    sale: Mapped["Sale"] = relationship("Sale", back_populates="items")
