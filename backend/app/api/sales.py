from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.sale import Sale
from app.services.inventory import create_sale, return_sale_item, InsufficientStockError

router = APIRouter(prefix="/sales", tags=["Продажи"])


class SaleItemIn(BaseModel):
    variant_id: int
    quantity: int = Field(gt=0)


class CreateSaleRequest(BaseModel):
    store_id: int
    items: list[SaleItemIn]
    comment: str | None = None


@router.post("/")
async def make_sale(
    data: CreateSaleRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not data.items:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    try:
        sale = await create_sale(
            session=db,
            store_id=data.store_id,
            seller_id=user.id,
            items=[{"variant_id": i.variant_id, "quantity": i.quantity} for i in data.items],
            comment=data.comment,
        )
        await db.commit()
        return {
            "id": sale.id,
            "total_amount": float(sale.total_amount),
            "items_count": sale.items_count,
            "message": "Продажа успешно оформлена",
        }
    except InsufficientStockError as e:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Недостаточно товара «{e.variant_name}». Доступно: {e.available} шт.",
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{store_id}/today")
async def today_sales(
    store_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone, timedelta
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(Sale)
        .where(Sale.store_id == store_id, Sale.created_at >= today_start)
        .options(selectinload(Sale.items))
        .order_by(Sale.created_at.desc())
    )
    sales = result.scalars().all()

    total = sum(float(s.total_amount) for s in sales)
    return {
        "count": len(sales),
        "total_amount": total,
        "sales": [
            {
                "id": s.id,
                "total": float(s.total_amount),
                "items_count": s.items_count,
                "created_at": s.created_at.isoformat(),
                "items": [
                    {
                        "id": i.id,
                        "name": i.product_name,
                        "variant": i.variant_info,
                        "qty": i.quantity,
                        "returned": i.returned_quantity,
                        "price": float(i.price_at_sale),
                    }
                    for i in s.items
                ],
            }
            for s in sales
        ],
    }
