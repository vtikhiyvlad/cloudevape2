from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.store import StoreUser
from app.models.product import Product, ProductVariant

router = Router()


@router.message(F.text == "📦 Остатки")
async def show_stock(message: Message):
    tg_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Сначала нажми /start")
            return

        link = await session.execute(
            select(StoreUser).where(StoreUser.user_id == user.id, StoreUser.is_active == True)
        )
        store_link = link.scalar_one_or_none()
        if not store_link:
            await message.answer("Ты не привязан к магазину.")
            return

        result = await session.execute(
            select(ProductVariant)
            .join(Product)
            .where(Product.store_id == store_link.store_id, ProductVariant.is_active == True)
            .options(selectinload(ProductVariant.product))
            .order_by(Product.name, ProductVariant.strength)
            .limit(50)
        )
        variants = result.scalars().all()

    if not variants:
        await message.answer("Товаров пока нет.")
        return

    lines = ["<b>Остатки (первые 50):</b>\n"]
    for v in variants:
        name = v.product.name if v.product else "?"
        info = []
        if v.strength:
            info.append(v.strength)
        if v.flavor:
            info.append(v.flavor)
        info_str = " / ".join(info) if info else ""
        mark = "🔴" if v.stock <= 0 else ("🟡" if v.stock <= v.low_stock_threshold else "🟢")
        lines.append(f"{mark} {name} {info_str} — <b>{v.stock}</b> шт.")

    await message.answer("\n".join(lines))


@router.message(F.text == "📊 Моя смена")
async def my_shift(message: Message):
    from datetime import datetime, timezone
    from app.models.sale import Sale

    tg_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Сначала нажми /start")
            return

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await session.execute(
            select(Sale).where(Sale.seller_id == user.id, Sale.created_at >= today_start)
        )
        sales = result.scalars().all()

    total = sum(float(s.total_amount) for s in sales)
    await message.answer(
        f"<b>Моя смена сегодня</b>\n\n"
        f"Продаж: <b>{len(sales)}</b>\n"
        f"Сумма: <b>{int(total)}₽</b>"
    )
