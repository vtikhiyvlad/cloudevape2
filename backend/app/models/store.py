from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Связи
    users: Mapped[list["StoreUser"]] = relationship("StoreUser", back_populates="store")
    categories: Mapped[list["Category"]] = relationship("Category", back_populates="store")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="store")


class StoreUser(Base):
    """Связь пользователя с магазином + роль внутри магазина"""
    __tablename__ = "store_users"
    __table_args__ = (UniqueConstraint("store_id", "user_id", name="uq_store_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20), default="seller")  # admin / seller
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    store: Mapped["Store"] = relationship("Store", back_populates="users")
    user: Mapped["User"] = relationship("User", back_populates="store_links")
