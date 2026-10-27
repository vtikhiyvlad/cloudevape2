"""Endpoints for manually testing and managing Evotor integration."""
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.config import get_settings
from app.models.user import User
from app.models.store import Store
from app.models.evotor import EvotorDocument, EvotorProductMapping
from app.integrations.evotor_client import EvotorClient, EvotorAPIError
from app.integrations.evotor_import import import_document

router = APIRouter(prefix="/evotor", tags=["Эвотор"])


def require_manager(user: User):
    if user.role not in {"sysadmin", "owner", "admin"}:
        raise HTTPException(status_code=403, detail="Только владелец или администратор")


class SyncRequest(BaseModel):
    evotor_store_id: str = Field(min_length=1, max_length=100)
    cloudvape_store_id: int = Field(gt=0)
    # Unix timestamp в миллисекундах. Обязательно задаётся явно, чтобы не
    # импортировать всю историю случайно.
    since_ms: int = Field(gt=0)


class MappingRequest(BaseModel):
    evotor_store_id: str = Field(min_length=1, max_length=100)
    cloudvape_store_id: int = Field(gt=0)
    evotor_product_id: str = Field(min_length=1, max_length=200)
    variant_id: int = Field(gt=0)


@router.post("/sync")
async def sync_documents(
    data: SyncRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Однократный ручной запуск импорта; сначала использовать на тестовой точке."""
    require_manager(user)
    token = get_settings().EVOTOR_TOKEN.strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="EVOTOR_TOKEN не задан в окружении backend",
        )

    store = await db.get(Store, data.cloudvape_store_id)
    if not store or not store.is_active:
        raise HTTPException(status_code=404, detail="Магазин CloudVape не найден/неактивен")

    client = EvotorClient(token)
    try:
        documents = await client.get_sales_documents(
            data.evotor_store_id, data.since_ms
        )
    except EvotorAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await client.close()

    counts = {
        "received": len(documents),
        "processed": 0,
        "duplicate": 0,
        "needs_mapping": 0,
        "review": 0,
        "ignored": 0,
        "errors": 0,
    }
    for doc in documents:
        try:
            # Каждый документ — отдельная транзакция: один плохой чек не ломает весь импорт.
            status = await import_document(
                db, data.evotor_store_id, data.cloudvape_store_id, doc
            )
            await db.commit()
            counts[status] = counts.get(status, 0) + 1
        except Exception:
            await db.rollback()
            counts["errors"] += 1
            # Не раскрываем внутренние ошибки/данные кассы в API-ответе.
    return {
        **counts,
        "message": "Импорт завершён. Проверь needs_mapping/review/errors перед включением автоматики.",
    }


@router.post("/mapping")
async def create_mapping(
    data: MappingRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_manager(user)
    store = await db.get(Store, data.cloudvape_store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин CloudVape не найден")

    from app.models.product import ProductVariant, Product
    variant = await db.get(ProductVariant, data.variant_id)
    if not variant:
        raise HTTPException(status_code=404, detail="Вариант товара не найден")
    product = await db.get(Product, variant.product_id)
    if not product or product.store_id != data.cloudvape_store_id:
        raise HTTPException(status_code=400, detail="Вариант не принадлежит выбранному магазину")

    existing = await db.scalar(select(EvotorProductMapping).where(
        EvotorProductMapping.evotor_store_id == data.evotor_store_id,
        EvotorProductMapping.evotor_product_id == data.evotor_product_id,
    ))
    if existing:
        existing.cloudvape_store_id = data.cloudvape_store_id
        existing.variant_id = data.variant_id
    else:
        db.add(EvotorProductMapping(
            evotor_store_id=data.evotor_store_id,
            cloudvape_store_id=data.cloudvape_store_id,
            evotor_product_id=data.evotor_product_id,
            variant_id=data.variant_id,
        ))
    await db.commit()
    return {"ok": True, "message": "Сопоставление сохранено"}


@router.get("/documents/{cloudvape_store_id}")
async def list_imported_documents(
    cloudvape_store_id: int,
    status: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_manager(user)
    query = select(EvotorDocument).where(
        EvotorDocument.cloudvape_store_id == cloudvape_store_id
    ).order_by(EvotorDocument.imported_at.desc()).limit(100)
    if status:
        query = query.where(EvotorDocument.status == status)
    rows = (await db.execute(query)).scalars().all()
    return [{
        "id": row.id,
        "evotor_store_id": row.evotor_store_id,
        "external_id": row.external_id,
        "type": row.document_type,
        "status": row.status,
        "error": row.error_text,
        "imported_at": row.imported_at.isoformat() if row.imported_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
    } for row in rows]
