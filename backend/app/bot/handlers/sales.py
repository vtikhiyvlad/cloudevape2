"""
Процесс продажи через бота.
Пока упрощённый, но рабочий вариант с защитой остатков.
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.store import StoreUser
from app.models.product import Category, Product, ProductVariant
from app.services.inventory import create_sale, InsufficientStockError

router = Router()


class SaleStates(StatesGroup):
    choosing_category = State()
    choosing_product = State()
    choosing_variant = State()
    choosing_quantity = State()
    confirm = State()


async def get_user_store(tg_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            return None, None

        link = await session.execute(
            select(StoreUser).where(StoreUser.user_id == user.id, StoreUser.is_active == True)
        )
        store_link = link.scalar_one_or_none()
        if not store_link:
            return user, None
        return user, store_link.store_id


@router.message(F.text == "🛒 Продажа")
async def start_sale(message: Message, state: FSMContext):
    user, store_id = await get_user_store(message.from_user.id)
    if not user or not store_id:
        await message.answer("Ты не привязан к магазину. Обратись к администратору.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category)
            .where(Category.store_id == store_id, Category.is_active == True)
            .order_by(Category.sort_order, Category.name)
        )
        categories = result.scalars().all()

    if not categories:
        await message.answer("В магазине пока нет категорий товаров.\nЗагрузите товары через веб-интерфейс.")
        return

    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"sale_cat:{c.id}")]
        for c in categories
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])

    await state.set_state(SaleStates.choosing_category)
    await state.update_data(store_id=store_id, user_id=user.id, cart=[])
    await message.answer(
        "<b>Выбери категорию:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "sale_cancel")
async def cancel_sale(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Продажа отменена.")
    await callback.answer()


@router.callback_query(F.data.startswith("sale_cat:"))
async def choose_category(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    store_id = data["store_id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Product)
            .where(Product.store_id == store_id, Product.category_id == cat_id, Product.is_active == True)
            .options(selectinload(Product.variants))
            .order_by(Product.name)
        )
        products = result.scalars().all()

    # Оставляем только товары с активными вариантами и остатком > 0
    available = []
    for p in products:
        vars_with_stock = [v for v in p.variants if v.is_active and v.stock > 0]
        if vars_with_stock:
            available.append((p, vars_with_stock))

    if not available:
        await callback.answer("В этой категории нет товаров в наличии", show_alert=True)
        return

    buttons = []
    for p, _ in available:
        buttons.append([InlineKeyboardButton(text=p.name, callback_data=f"sale_prod:{p.id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sale_back_cat")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])

    await state.set_state(SaleStates.choosing_product)
    await state.update_data(category_id=cat_id)
    await callback.message.edit_text(
        "<b>Выбери товар:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sale_prod:"))
async def choose_product(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ProductVariant)
            .where(ProductVariant.product_id == product_id, ProductVariant.is_active == True, ProductVariant.stock > 0)
            .order_by(ProductVariant.strength, ProductVariant.flavor)
        )
        variants = result.scalars().all()

        prod = await session.get(Product, product_id)
        product_name = prod.name if prod else "Товар"

    if not variants:
        await callback.answer("Нет вариантов в наличии", show_alert=True)
        return

    buttons = []
    for v in variants:
        label_parts = []
        if v.strength:
            label_parts.append(v.strength)
        if v.flavor:
            label_parts.append(v.flavor)
        label = " / ".join(label_parts) if label_parts else f"Вариант #{v.id}"
        label += f" — {int(v.price)}₽ (ост. {v.stock})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sale_var:{v.id}")])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sale_back_prod")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])

    await state.set_state(SaleStates.choosing_variant)
    await state.update_data(product_id=product_id, product_name=product_name)
    await callback.message.edit_text(
        f"<b>{product_name}</b>\nВыбери вариант:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sale_var:"))
async def choose_variant(callback: CallbackQuery, state: FSMContext):
    variant_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        variant = await session.get(ProductVariant, variant_id)

    if not variant or variant.stock <= 0:
        await callback.answer("Товар закончился", show_alert=True)
        return

    await state.set_state(SaleStates.choosing_quantity)
    await state.update_data(
        variant_id=variant_id,
        max_stock=variant.stock,
        price=float(variant.price),
        strength=variant.strength,
        flavor=variant.flavor,
    )

    # Кнопки количества
    qty_buttons = []
    row = []
    for q in [1, 2, 3, 5]:
        if q <= variant.stock:
            row.append(InlineKeyboardButton(text=str(q), callback_data=f"sale_qty:{q}"))
    if row:
        qty_buttons.append(row)

    qty_buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])

    info = []
    if variant.strength:
        info.append(variant.strength)
    if variant.flavor:
        info.append(variant.flavor)
    info_str = " / ".join(info) if info else ""

    await callback.message.edit_text(
        f"<b>Количество</b>\n"
        f"{info_str}\n"
        f"Цена: <b>{int(variant.price)}₽</b>\n"
        f"В наличии: <b>{variant.stock}</b> шт.\n\n"
        f"Выбери количество:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=qty_buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sale_qty:"))
async def choose_quantity(callback: CallbackQuery, state: FSMContext):
    qty = int(callback.data.split(":")[1])
    data = await state.get_data()

    if qty > data["max_stock"]:
        await callback.answer(f"Максимум {data['max_stock']} шт.", show_alert=True)
        return

    total = data["price"] * qty
    info = []
    if data.get("strength"):
        info.append(data["strength"])
    if data.get("flavor"):
        info.append(data["flavor"])
    info_str = " / ".join(info) if info else ""

    text = (
        f"<b>Подтверждение продажи</b>\n\n"
        f"Товар: <b>{data.get('product_name', '')}</b>\n"
        f"Вариант: {info_str}\n"
        f"Количество: <b>{qty}</b> шт.\n"
        f"Сумма: <b>{int(total)}₽</b>\n"
        f"Останется на складе: <b>{data['max_stock'] - qty}</b> шт.\n\n"
        f"Всё верно?"
    )

    buttons = [
        [
            InlineKeyboardButton(text="✅ Продать", callback_data=f"sale_confirm:{qty}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel"),
        ]
    ]

    await state.set_state(SaleStates.confirm)
    await state.update_data(quantity=qty)
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("sale_confirm:"))
async def confirm_sale(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    qty = int(callback.data.split(":")[1])

    try:
        async with AsyncSessionLocal() as session:
            sale = await create_sale(
                session=session,
                store_id=data["store_id"],
                seller_id=data["user_id"],
                items=[{"variant_id": data["variant_id"], "quantity": qty}],
            )
            await session.commit()

        await state.clear()
        await callback.message.edit_text(
            f"✅ <b>Продано!</b>\n\n"
            f"Сумма: <b>{int(sale.total_amount)}₽</b>\n"
            f"Чек №{sale.id}"
        )
    except InsufficientStockError as e:
        await callback.message.edit_text(
            f"❌ Недостаточно товара.\nДоступно только: {e.available} шт."
        )
        await state.clear()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")
        await state.clear()

    await callback.answer()
