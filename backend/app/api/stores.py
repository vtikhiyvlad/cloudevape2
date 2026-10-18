from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.api.deps import get_current_user, get_owner_user
from app.models.user import User
from app.models.store import Store, StoreUser

router = APIRouter(prefix="/stores", tags=["Магазины"])


class StoreCreate(BaseModel):
    name: str


class StoreOut(BaseModel):
    id: int
    name: str
    is_active: bool

    class Config:
        from_attributes = True


@router.get("/", response_model=list[StoreOut])
async def list_stores(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role == "owner":
        result = await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))
        return result.scalars().all()

    # Для остальных — только свои магазины
    result = await db.execute(
        select(Store)
        .join(StoreUser, StoreUser.store_id == Store.id)
        .where(StoreUser.user_id == user.id, StoreUser.is_active == True, Store.is_active == True)
    )
    return result.scalars().all()


@router.post("/", response_model=StoreOut)
async def create_store(
    data: StoreCreate,
    user: User = Depends(get_owner_user),
    db: AsyncSession = Depends(get_db),
):
    store = Store(name=data.name.strip())
    db.add(store)
    await db.flush()
    return store


@router.get("/{store_id}", response_model=StoreOut)
async def get_store(
    store_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Store).where(Store.id == store_id))
    store = result.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    return store
