from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class LoginCode(Base):
    """Одноразовый код для входа на сайт через Telegram-бота"""
    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
