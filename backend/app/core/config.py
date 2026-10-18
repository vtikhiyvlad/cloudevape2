from pydantic_settings import BaseSettings
from functools import lru_cache
from pydantic import field_validator


class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    SECRET_KEY: str = "change-me"
    OWNER_PASSWORD: str = "admin123"
    DATABASE_URL: str = "postgresql+asyncpg://cloudvape:cloudvape_secret@db:5432/cloudvape"
    DEBUG: bool = True

    # JWT
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 дней

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_postgres_url(cls, v: str) -> str:
        """Render отдаёт postgres://, а нам нужен postgresql+asyncpg://"""
        if v and v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v and v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
