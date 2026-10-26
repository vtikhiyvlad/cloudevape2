from datetime import datetime, date
from sqlalchemy import String, Boolean, DateTime, Date, ForeignKey, Integer, Text, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class InventoryTask(Base):
    """Назначенная админом инвентаризация"""
    __tablename__ = "inventory_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Инвентаризация")
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    # JSON-список category_id через запятую, пусто = все категории
    category_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    counts: Mapped[list["InventoryCount"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class InventoryCount(Base):
    """Одна проведённая инвентаризация продавцом (не чаще 1 раза в день)"""
    __tablename__ = "inventory_counts"
    __table_args__ = (
        UniqueConstraint("task_id", "user_id", "count_date", name="uq_inventory_once_per_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("inventory_tasks.id", ondelete="CASCADE"), index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    count_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # in_progress / done
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)  # остатки применены админом

    task: Mapped["InventoryTask"] = relationship(back_populates="counts")
    lines: Mapped[list["InventoryLine"]] = relationship(back_populates="count", cascade="all, delete-orphan")


class InventoryLine(Base):
    """Строка: учётный остаток vs факт"""
    __tablename__ = "inventory_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("inventory_counts.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"))
    system_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(200), default="")
    variant_info: Mapped[str | None] = mapped_column(String(200), nullable=True)

    count: Mapped["InventoryCount"] = relationship(back_populates="lines")
