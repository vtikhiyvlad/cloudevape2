from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.core.database import engine, Base, AsyncSessionLocal
from app.core.config import get_settings
from app.core.security import get_password_hash
from app.models import User
from sqlalchemy import select

from app.api import auth, stores, products, sales

settings = get_settings()


async def create_owner_if_not_exists():
    """Создаём первого владельца при старте, если его нет"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.role == "owner"))
        owner = result.scalar_one_or_none()
        if not owner:
            owner = User(
                full_name="Владелец",
                username="owner",
                role="owner",
                password_hash=get_password_hash(settings.OWNER_PASSWORD),
                is_active=True,
            )
            session.add(owner)
            await session.commit()
            print("✅ Создан владелец системы (логин: owner)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await create_owner_if_not_exists()
    print("🚀 CloudVape backend запущен")
    yield
    await engine.dispose()


app = FastAPI(
    title="CloudVape",
    description="CRM для вейп-шопов",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Подключаем API
app.include_router(auth.router, prefix="/api")
app.include_router(stores.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(sales.router, prefix="/api")


@app.get("/")
async def root():
    return {"message": "CloudVape API работает", "version": "0.1.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
