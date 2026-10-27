"""Безопасный импорт документов Эвотора в существующий учёт CloudVape.

Важно: документ SELL списывает остатки только после сопоставления всех строк.
PAYBACK пока сохраняется на проверку: автоматический возврат требует связи с
исходной продажей и проверки схемы документов конкретного магазина.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evotor import EvotorDocument, EvotorProductMapping
from app.models.product import ProductVariant, Product
from app.models.sale import Sale, SaleItem
from app.services.inventory import change_stock


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _positions(doc: dict[str, Any]) -> list[dict[str, Any]]:
    body = doc.get("body") or {}
    positions = body.get("positions") if isinstance(body, dict) else None
    return positions if isinstance(positions, list) else []


async def _resolve_variant(
    session: AsyncSession,
    evotor_store_id: str,
    cloudvape_store_id: int,
    position: dict[str, Any],
) -> ProductVariant | None:
    product_id = position.get("product_id")
    if product_id:
        mapping = await session.scalar(
            select(EvotorProductMapping).where(
                EvotorProductMapping.evotor_store_id == evotor_store_id,
                EvotorProductMapping.evotor_product_id == str(product_id),
                EvotorProductMapping.cloudvape_store_id == cloudvape_store_id,
            )
        )
        if mapping:
            variant = await session.get(ProductVariant, mapping.variant_id)
            if variant and variant.is_active:
                product = await session.get(Product, variant.product_id)
                if product and product.store_id == cloudvape_store_id and product.is_active:
                    return variant

    barcode = position.get("bar_code") or position.get("barcode")
    if barcode:
        rows = (await session.execute(
            select(ProductVariant)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                Product.store_id == cloudvape_store_id,
                Product.is_active.is_(True),
                ProductVariant.is_active.is_(True),
                ProductVariant.barcode == str(barcode),
            )
            .limit(2)
        )).scalars().all()
        # Автоматическое сопоставление разрешено только при единственном совпадении.
        if len(rows) == 1:
            return rows[0]
    return None


async def import_document(
    session: AsyncSession,
    evotor_store_id: str,
    cloudvape_store_id: int,
    doc: dict[str, Any],
) -> str:
    """Возвращает processed / duplicate / needs_mapping / review / ignored."""
    external_id = doc.get("id")
    doc_type = doc.get("type")
    if not external_id or doc_type not in {"SELL", "PAYBACK"}:
        return "ignored"

    # Сохраняем документ в той же транзакции, что и продажу/движения.
    stmt = (
        insert(EvotorDocument)
        .values(
            evotor_store_id=evotor_store_id,
            cloudvape_store_id=cloudvape_store_id,
            external_id=str(external_id),
            document_type=str(doc_type),
            document_time=_parse_datetime(doc.get("close_date")),
            status="pending",
            payload=doc,
        )
        .on_conflict_do_nothing(constraint="uq_evotor_document_per_store")
        .returning(EvotorDocument.id)
    )
    document_id = (await session.execute(stmt)).scalar_one_or_none()
    if document_id is None:
        return "duplicate"

    record = await session.get(EvotorDocument, document_id)
    if doc_type == "PAYBACK":
        record.status = "review"
        record.error_text = (
            "Возврат импортирован, но автоматическое изменение остатка отключено: "
            "нужно сопоставить его с исходной продажей."
        )
        return "review"

    positions = _positions(doc)
    if not positions:
        record.status = "error"
        record.error_text = "В документе SELL не найден body.positions"
        return "review"

    # Сначала проверяем весь чек, и только затем меняем остатки.
    resolved: list[tuple[dict[str, Any], ProductVariant, int, Decimal]] = []
    unresolved: list[str] = []
    for pos in positions:
        variant = await _resolve_variant(
            session, evotor_store_id, cloudvape_store_id, pos
        )
        if not variant:
            unresolved.append(
                str(pos.get("product_name") or pos.get("product_id") or pos.get("bar_code") or "неизвестный товар")
            )
            continue
        try:
            qty_decimal = Decimal(str(pos.get("quantity", 0)))
            price = Decimal(str(pos.get("price", 0)))
        except (InvalidOperation, TypeError, ValueError):
            unresolved.append(str(pos.get("product_name") or "позиция с некорректным количеством/ценой"))
            continue
        if qty_decimal <= 0 or qty_decimal != qty_decimal.to_integral_value():
            unresolved.append(
                f"{pos.get('product_name', 'товар')}: неподдерживаемое количество {qty_decimal}"
            )
            continue
        resolved.append((pos, variant, int(qty_decimal), price))

    if unresolved:
        record.status = "needs_mapping"
        record.error_text = "Не обработаны позиции: " + "; ".join(unresolved[:20])
        return "needs_mapping"

    sale = Sale(
        store_id=cloudvape_store_id,
        seller_id=None,
        total_amount=Decimal("0"),
        items_count=0,
        status="completed",
        comment=f"Импорт из Эвотора: {external_id}",
        created_at=_parse_datetime(doc.get("close_date")) or datetime.now(timezone.utc),
    )
    session.add(sale)
    await session.flush()

    total = Decimal("0")
    items_count = 0
    # Одинаковый порядок блокировок снижает риск deadlock.
    for pos, variant, quantity, price in sorted(resolved, key=lambda row: row[1].id):
        # Проверяем принадлежность варианта нужному магазину ещё раз перед списанием.
        product = await session.get(Product, variant.product_id)
        if not product or product.store_id != cloudvape_store_id:
            raise ValueError("Сопоставленный товар принадлежит другому магазину")

        await change_stock(
            session=session,
            variant_id=variant.id,
            quantity_change=-quantity,
            movement_type="sale",
            user_id=None,
            store_id=cloudvape_store_id,
            comment=f"Автоимпорт Эвотор, документ {external_id}",
            sale_id=sale.id,
        )
        info_parts = [x for x in [variant.strength, variant.flavor] if x]
        line_total = price * quantity
        session.add(SaleItem(
            sale_id=sale.id,
            variant_id=variant.id,
            product_name=product.name,
            variant_info=" / ".join(info_parts) if info_parts else None,
            quantity=quantity,
            price_at_sale=price,
            total=line_total,
        ))
        total += line_total
        items_count += quantity

    sale.total_amount = total
    sale.items_count = items_count
    record.status = "processed"
    record.processed_at = datetime.now(timezone.utc)
    record.error_text = None
    await session.flush()
    return "processed"
