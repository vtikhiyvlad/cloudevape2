"""
Сервис работы с остатками.
Главная защита от пересорта — атомарное списание с проверкой.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.product import ProductVariant
from app.models.inventory import StockMovement
from app.models.sale import Sale, SaleItem
from decimal import Decimal


class InsufficientStockError(Exception):
    def __init__(self, available: int, requested: int, variant_name: str = ""):
        self.available = available
        self.requested = requested
        self.variant_name = variant_name
        super().__init__(f"Недостаточно товара. Доступно: {available}, запрошено: {requested}")


async def get_variant_for_update(session: AsyncSession, variant_id: int) -> ProductVariant | None:
    """Получаем вариант с блокировкой строки (FOR UPDATE)"""
    result = await session.execute(
        select(ProductVariant)
        .where(ProductVariant.id == variant_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def change_stock(
    session: AsyncSession,
    variant_id: int,
    quantity_change: int,
    movement_type: str,
    user_id: int | None,
    store_id: int,
    comment: str | None = None,
    sale_id: int | None = None,
) -> ProductVariant:
    """
    Атомарно меняет остаток.
    quantity_change: отрицательное = списание, положительное = приход
    """
    variant = await get_variant_for_update(session, variant_id)
    if not variant:
        raise ValueError("Вариант товара не найден")

    stock_before = variant.stock
    new_stock = stock_before + quantity_change

    if new_stock < 0:
        raise InsufficientStockError(
            available=stock_before,
            requested=abs(quantity_change),
            variant_name=f"{variant.strength or ''} {variant.flavor or ''}".strip()
        )

    variant.stock = new_stock

    movement = StockMovement(
        variant_id=variant_id,
        user_id=user_id,
        store_id=store_id,
        movement_type=movement_type,
        quantity_change=quantity_change,
        stock_before=stock_before,
        stock_after=new_stock,
        comment=comment,
        sale_id=sale_id,
    )
    session.add(movement)
    await session.flush()
    return variant


async def create_sale(
    session: AsyncSession,
    store_id: int,
    seller_id: int,
    items: list[dict],  # [{"variant_id": 1, "quantity": 2}, ...]
    comment: str | None = None,
) -> Sale:
    """
    Создаёт продажу с атомарным списанием всех позиций.
    Если хотя бы по одной позиции не хватает — откатывает всё.
    """
    sale = Sale(
        store_id=store_id,
        seller_id=seller_id,
        comment=comment,
        status="completed",
    )
    session.add(sale)
    await session.flush()

    total_amount = Decimal("0")
    items_count = 0

    # Сортировка по variant_id — один порядок блокировок, меньше deadlock при параллельных продажах
    ordered = sorted(items, key=lambda x: int(x["variant_id"]))

    for item_data in ordered:
        variant_id = item_data["variant_id"]
        quantity = item_data["quantity"]

        if quantity <= 0:
            raise ValueError("Количество должно быть больше 0")

        variant = await get_variant_for_update(session, variant_id)
        if not variant or not variant.is_active:
            raise ValueError(f"Товар (id={variant_id}) не найден или неактивен")

        # Подтягиваем название товара
        await session.refresh(variant, ["product"])
        product_name = variant.product.name if variant.product else "Товар"
        variant_info_parts = []
        if variant.strength:
            variant_info_parts.append(variant.strength)
        if variant.flavor:
            variant_info_parts.append(variant.flavor)
        variant_info = " / ".join(variant_info_parts) if variant_info_parts else None

        # Списание
        await change_stock(
            session=session,
            variant_id=variant_id,
            quantity_change=-quantity,
            movement_type="sale",
            user_id=seller_id,
            store_id=store_id,
            sale_id=sale.id,
        )

        line_total = variant.price * quantity
        sale_item = SaleItem(
            sale_id=sale.id,
            variant_id=variant_id,
            product_name=product_name,
            variant_info=variant_info,
            quantity=quantity,
            price_at_sale=variant.price,
            total=line_total,
        )
        session.add(sale_item)

        total_amount += line_total
        items_count += quantity

    sale.total_amount = total_amount
    sale.items_count = items_count
    await session.flush()
    return sale


async def return_sale_item(
    session: AsyncSession,
    sale_item_id: int,
    quantity: int,
    user_id: int,
    store_id: int,
) -> SaleItem:
    """Возврат части или всей позиции продажи"""
    result = await session.execute(
        select(SaleItem).where(SaleItem.id == sale_item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise ValueError("Позиция продажи не найдена")

    available_to_return = item.quantity - item.returned_quantity
    if quantity <= 0 or quantity > available_to_return:
        raise ValueError(f"Можно вернуть от 1 до {available_to_return} шт.")

    if item.variant_id:
        await change_stock(
            session=session,
            variant_id=item.variant_id,
            quantity_change=quantity,
            movement_type="return",
            user_id=user_id,
            store_id=store_id,
            sale_id=item.sale_id,
            comment=f"Возврат позиции #{item.id}",
        )

    item.returned_quantity += quantity
    await session.flush()
    return item
