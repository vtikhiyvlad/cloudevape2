from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, BigInteger, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # для веб-входа
    role: Mapped[str] = mapped_column(String(20), default="seller")  # owner / admin / seller
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Связи
    store_links: Mapped[list["StoreUser"]] = relationship("StoreUser", back_populates="user")
