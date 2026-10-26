from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import secrets

from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.store import StoreUser, Store
from app.models.login_code import LoginCode

router = Router()


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌐 Войти на сайт")],
            [KeyboardButton(text="ℹ️ Помощь")],
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
                f"Твой Telegram ID: <code>{tg_id}</code>\n\n"
                "Передай ID администратору — он привяжет тебя к магазину.\n\n"
                "Продажи и инвентаризация — только на <b>сайте</b>.\n"
                "Кнопка «🌐 Войти на сайт» выдаёт код для входа.",
                reply_markup=main_keyboard(),
            )
            return

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
                f"Telegram ID: <code>{tg_id}</code>\n"
                "Админ ещё не привязал тебя к магазину.\n\n"
                "После привязки — «🌐 Войти на сайт».",
                reply_markup=main_keyboard(),
            )
            return

        store_names = ", ".join(link.Store.name for link in links)
        await message.answer(
            f"Привет, <b>{user.full_name}</b>!\n"
            f"Магазин: <b>{store_names}</b>\n\n"
            "Продажи и инвентаризация — на сайте.\n"
            "Нажми «🌐 Войти на сайт» → код → веб-касса.",
            reply_markup=main_keyboard(),
        )


@router.message(F.text == "🌐 Войти на сайт")
@router.message(Command("web"))
async def web_login_code(message: Message):
    tg_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Сначала нажми /start")
            return

        recent = await session.execute(
            select(LoginCode)
            .where(LoginCode.user_id == user.id)
            .order_by(LoginCode.created_at.desc())
            .limit(1)
        )
        last = recent.scalar_one_or_none()
        if last and last.created_at:
            created = last.created_at if last.created_at.tzinfo else last.created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created < timedelta(seconds=30):
                await message.answer("Подожди 30 секунд перед новым кодом.")
                return

        old = await session.execute(
            select(LoginCode).where(LoginCode.user_id == user.id, LoginCode.used == False)
        )
        for c in old.scalars().all():
            c.used = True

        code = f"{secrets.randbelow(100_000_000):08d}"
        session.add(LoginCode(
            user_id=user.id,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=3),
            used=False,
        ))
        await session.commit()

    await message.answer(
        f"🔐 <b>Код входа на сайт</b>\n\n"
        f"<code>{code}</code>\n\n"
        f"Действует <b>3 минуты</b>, один раз.\n\n"
        f"1. Открой сайт CloudVape\n"
        f"2. «Войти через Telegram»\n"
        f"3. Введи код\n\n"
        f"Никому не пересылай."
    )


@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "<b>CloudVape — бот</b>\n\n"
        "• Регистрация продавца\n"
        "• Код входа на сайт\n"
        "• Админу сюда приходят отчёты\n\n"
        "Продажи и инвентаризация — только в веб-версии.\n\n"
        "/start — регистрация\n"
        "/web — код входа"
    )


@router.message(F.text.in_({
    "🛒 Продажа", "📦 Остатки", "📊 Моя смена", "↩️ Возврат",
    "📋 Инвентаризация", "🌐 Открыть веб-кассу",
}))
async def removed_features(message: Message):
    await message.answer(
        "Продажи перенесены на сайт.\n"
        "Нажми «🌐 Войти на сайт»."
    )
