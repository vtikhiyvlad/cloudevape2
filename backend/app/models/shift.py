from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, DateTime, ForeignKey, Integer, Numeric, Text, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Shift(Base):
    """Рабочая смена / день магазина."""
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Итоги при закрытии
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    total_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_card: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    # Сколько наличных в ящике ввёл продавец
    cash_drawer: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
