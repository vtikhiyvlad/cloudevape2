from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import random
import string

from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.store import StoreUser, Store
from app.models.login_code import LoginCode

router = Router()


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Продажа")],
            [KeyboardButton(text="📦 Остатки"), KeyboardButton(text="📊 Моя смена")],
            [KeyboardButton(text="🌐 Вход на сайт")],
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
                "Обратись к администратору, чтобы тебя добавили в магазин.\n\n"
                "Чтобы войти на сайт — нажми «🌐 Вход на сайт».",
                reply_markup=main_keyboard(),
            )
            return

        # обновим имя если изменилось
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            await session.commit()

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


@router.message(F.text == "🌐 Вход на сайт")
@router.message(Command("web"))
async def web_login_code(message: Message):
    """Выдаёт одноразовый код для входа на сайт"""
    tg_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Сначала нажми /start")
            return

        # инвалидируем старые коды
        old = await session.execute(
            select(LoginCode).where(LoginCode.user_id == user.id, LoginCode.used == False)
        )
        for c in old.scalars().all():
            c.used = True

        code = "".join(random.choices(string.digits, k=6))
        lc = LoginCode(
            user_id=user.id,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            used=False,
        )
        session.add(lc)
        await session.commit()

    await message.answer(
        f"🔐 <b>Код для входа на сайт</b>\n\n"
        f"<code>{code}</code>\n\n"
        f"Действует <b>5 минут</b>.\n"
        f"На сайте: Войти → «Через Telegram» → введи этот код.\n\n"
        f"Отдельный логин/пароль не нужен."
    )
