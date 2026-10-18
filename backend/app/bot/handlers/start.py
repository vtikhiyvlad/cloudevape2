from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.store import StoreUser, Store

router = Router()


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Продажа")],
            [KeyboardButton(text="📦 Остатки"), KeyboardButton(text="📊 Моя смена")],
            [KeyboardButton(text="↩️ Возврат")],
        ],
        resize_keyboard=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    tg_id = message.from_user.id
    full_name = message.from_user.full_name or ""
    username = message.from_user.username

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()

        if not user:
            # Новый пользователь — пока просто регистрируем как seller без магазина
            user = User(
                telegram_id=tg_id,
                full_name=full_name,
                username=username,
                role="seller",
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await message.answer(
                f"Привет, {full_name}!\n\n"
                "Ты зарегистрирован в CloudVape.\n"
                "Обратись к администратору, чтобы тебя добавили в магазин.",
                reply_markup=main_keyboard(),
            )
            return

        # Проверяем, есть ли привязка к магазину
        link_result = await session.execute(
            select(StoreUser, Store)
            .join(Store, Store.id == StoreUser.store_id)
            .where(StoreUser.user_id == user.id, StoreUser.is_active == True)
        )
        links = link_result.all()

        if not links:
            await message.answer(
                f"Привет, {user.full_name}!\n\n"
                "Ты пока не привязан ни к одному магазину.\n"
                "Обратись к администратору.",
                reply_markup=main_keyboard(),
            )
            return

        store_names = ", ".join(link.Store.name for link in links)
        await message.answer(
            f"Привет, <b>{user.full_name}</b>!\n"
            f"Магазин: <b>{store_names}</b>\n\n"
            "Выбери действие:",
            reply_markup=main_keyboard(),
        )
