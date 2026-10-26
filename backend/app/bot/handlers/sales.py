"""
Продажа через бота с корзиной (мульти-чек).
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
    cart_view = State()


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


def cart_summary_text(cart: list) -> str:
    if not cart:
        return "Корзина пуста"
    lines = ["<b>Корзина:</b>"]
    total = 0
    for i, item in enumerate(cart, 1):
        lines.append(f"{i}. {item['name']} × {item['qty']} = {int(item['price'] * item['qty'])}₽")
        total += item["price"] * item["qty"]
    lines.append(f"\n<b>Итого: {int(total)}₽</b>")
    return "\n".join(lines)


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
        await message.answer("В магазине пока нет категорий.\nЗагрузите товары через веб-интерфейс.")
        return

    buttons = [[InlineKeyboardButton(text=c.name, callback_data=f"sale_cat:{c.id}")] for c in categories]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])

    await state.set_state(SaleStates.choosing_category)
    await state.update_data(store_id=store_id, user_id=user.id, cart=[])
    await message.answer("<b>Выбери категорию:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


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

    available = []
    for p in products:
        if any(v.is_active and v.stock > 0 for v in p.variants):
            available.append(p)

    if not available:
        await callback.answer("В этой категории нет товаров в наличии", show_alert=True)
        return

    buttons = [[InlineKeyboardButton(text=p.name, callback_data=f"sale_prod:{p.id}")] for p in available]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sale_back_cats")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])

    await state.set_state(SaleStates.choosing_product)
    await state.update_data(category_id=cat_id)
    await callback.message.edit_text("<b>Выбери товар:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "sale_back_cats")
async def back_cats(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category)
            .where(Category.store_id == data["store_id"], Category.is_active == True)
            .order_by(Category.sort_order, Category.name)
        )
        categories = result.scalars().all()
    buttons = [[InlineKeyboardButton(text=c.name, callback_data=f"sale_cat:{c.id}")] for c in categories]
    cart = data.get("cart") or []
    if cart:
        buttons.append([InlineKeyboardButton(text=f"🛒 Корзина ({len(cart)})", callback_data="sale_cart")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])
    await state.set_state(SaleStates.choosing_category)
    await callback.message.edit_text("<b>Выбери категорию:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
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
        parts = []
        if v.strength:
            parts.append(v.strength)
        if v.flavor:
            parts.append(v.flavor)
        label = " / ".join(parts) if parts else f"Вариант #{v.id}"
        label += f" — {int(v.price)}₽ (ост. {v.stock})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sale_var:{v.id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sale_back_cats")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])

    await state.set_state(SaleStates.choosing_variant)
    await state.update_data(product_id=product_id, product_name=product_name)
    await callback.message.edit_text(f"<b>{product_name}</b>\nВыбери вариант:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
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
        f"<b>Количество</b>\n{info_str}\nЦена: <b>{int(variant.price)}₽</b>\nВ наличии: <b>{variant.stock}</b> шт.\n\nВыбери количество:",
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

    info = []
    if data.get("strength"):
        info.append(data["strength"])
    if data.get("flavor"):
        info.append(data["flavor"])
    info_str = " / ".join(info) if info else ""
    name = data.get("product_name", "Товар")
    if info_str:
        name = f"{name} ({info_str})"

    cart = data.get("cart") or []
    cart.append({
        "variant_id": data["variant_id"],
        "name": name,
        "qty": qty,
        "price": data["price"],
    })
    await state.update_data(cart=cart)
    await state.set_state(SaleStates.cart_view)

    text = cart_summary_text(cart) + "\n\nДобавить ещё или оформить?"
    buttons = [
        [InlineKeyboardButton(text="➕ Ещё товар", callback_data="sale_add_more")],
        [InlineKeyboardButton(text="✅ Оформить чек", callback_data="sale_checkout")],
        [InlineKeyboardButton(text="🗑 Очистить", callback_data="sale_clear_cart")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "sale_add_more")
async def add_more(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category)
            .where(Category.store_id == data["store_id"], Category.is_active == True)
            .order_by(Category.sort_order, Category.name)
        )
        categories = result.scalars().all()
    buttons = [[InlineKeyboardButton(text=c.name, callback_data=f"sale_cat:{c.id}")] for c in categories]
    cart = data.get("cart") or []
    buttons.append([InlineKeyboardButton(text=f"🛒 Корзина ({len(cart)}) · Оформить", callback_data="sale_checkout")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")])
    await state.set_state(SaleStates.choosing_category)
    await callback.message.edit_text(
        cart_summary_text(cart) + "\n\n<b>Выбери категорию:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "sale_cart")
async def show_cart(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart") or []
    text = cart_summary_text(cart) + "\n\nДобавить ещё или оформить?"
    buttons = [
        [InlineKeyboardButton(text="➕ Ещё товар", callback_data="sale_add_more")],
        [InlineKeyboardButton(text="✅ Оформить чек", callback_data="sale_checkout")],
        [InlineKeyboardButton(text="🗑 Очистить", callback_data="sale_clear_cart")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sale_cancel")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "sale_clear_cart")
async def clear_cart(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cart=[])
    await state.clear()
    await callback.message.edit_text("Корзина очищена.")
    await callback.answer()


@router.callback_query(F.data == "sale_checkout")
async def checkout(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart") or []
    if not cart:
        await callback.answer("Корзина пуста", show_alert=True)
        return

    items = [{"variant_id": i["variant_id"], "quantity": i["qty"]} for i in cart]
    try:
        async with AsyncSessionLocal() as session:
            sale = await create_sale(
                session=session,
                store_id=data["store_id"],
                seller_id=data["user_id"],
                items=items,
            )
            await session.commit()
        await state.clear()
        await callback.message.edit_text(
            f"✅ <b>Чек оформлен!</b>\n\n"
            f"Позиций: {len(cart)}\n"
            f"Сумма: <b>{int(sale.total_amount)}₽</b>\n"
            f"Чек №{sale.id}"
        )
    except InsufficientStockError as e:
        await callback.message.edit_text(
            f"❌ Недостаточно товара «{e.variant_name}».\nДоступно: {e.available} шт.\nКорзина не списана — начни заново."
        )
        await state.clear()
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")
        await state.clear()
    await callback.answer()
