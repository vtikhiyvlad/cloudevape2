from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    SECRET_KEY: str = "change-me-in-production-use-long-random"
    OWNER_PASSWORD: str = "admin123"
    DATABASE_URL: str = "postgresql+asyncpg://cloudvape:cloudvape_secret@db:5432/cloudvape"
    DEBUG: bool = False
    # Публичный URL сайта для magic-ссылок из бота (https://xxx.onrender.com)
    WEB_BASE_URL: str = ""
    EVOTOR_TOKEN: str = ""

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_postgres_url(cls, v: str) -> str:
        if not v:
            return v
        # Render/Heroku: postgres:// → postgresql+asyncpg://
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
