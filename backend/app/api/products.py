from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.product import Category, Product, ProductVariant
from app.services.excel_import import import_products_from_excel

router = APIRouter(prefix="/products", tags=["Товары"])


class VariantOut(BaseModel):
    id: int
    strength: str | None
    flavor: str | None
    price: Decimal
    stock: int
    is_active: bool

    class Config:
        from_attributes = True


class ProductOut(BaseModel):
    id: int
    name: str
    category_id: int | None
    category_name: str | None = None
    variants: list[VariantOut] = []

    class Config:
        from_attributes = True


@router.get("/{store_id}/categories")
async def list_categories(
    store_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Category)
        .where(Category.store_id == store_id, Category.is_active == True)
        .order_by(Category.sort_order, Category.name)
    )
    return [{"id": c.id, "name": c.name} for c in result.scalars().all()]


@router.get("/{store_id}/list")
async def list_products(
    store_id: int,
    category_id: int | None = None,
    search: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Product)
        .where(Product.store_id == store_id, Product.is_active == True)
        .options(selectinload(Product.variants), selectinload(Product.category))
        .order_by(Product.name)
    )
    if category_id:
        q = q.where(Product.category_id == category_id)
    if search:
        q = q.where(Product.name.ilike(f"%{search}%"))

    result = await db.execute(q)
    products = result.scalars().all()

    out = []
    for p in products:
        active_variants = [v for v in p.variants if v.is_active]
        out.append({
            "id": p.id,
            "name": p.name,
            "category_id": p.category_id,
            "category_name": p.category.name if p.category else None,
            "variants": [
                {
                    "id": v.id,
                    "strength": v.strength,
                    "flavor": v.flavor,
                    "price": float(v.price),
                    "stock": v.stock,
                    "is_active": v.is_active,
                }
                for v in active_variants
            ],
        })
    return out


@router.post("/{store_id}/import-excel")
async def import_excel(
    store_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Нужен файл Excel (.xlsx)")

    content = await file.read()
    stats = await import_products_from_excel(db, store_id, user.id, content)
    await db.commit()
    return stats


@router.get("/{store_id}/stock")
async def stock_overview(
    store_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Обзор остатков с цветовой индикацией"""
    result = await db.execute(
        select(ProductVariant)
        .join(Product)
        .where(Product.store_id == store_id, ProductVariant.is_active == True)
        .options(selectinload(ProductVariant.product).selectinload(Product.category))
        .order_by(Product.name)
    )
    variants = result.scalars().all()

    items = []
    for v in variants:
        status = "ok"
        if v.stock <= 0:
            status = "out"
        elif v.stock <= v.low_stock_threshold:
            status = "low"

        items.append({
            "variant_id": v.id,
            "product_name": v.product.name if v.product else "",
            "category": v.product.category.name if v.product and v.product.category else "",
            "strength": v.strength,
            "flavor": v.flavor,
            "price": float(v.price),
            "stock": v.stock,
            "status": status,
        })
    return items
