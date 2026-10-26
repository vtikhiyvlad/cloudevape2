from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from sqlalchemy import select
from datetime import datetime, timezone, timedelta

from app.core.database import AsyncSessionLocal
from app.core.config import get_settings
from app.core.security import new_login_token, new_login_pin
from app.models.user import User
from app.models.store import StoreUser, Store
from app.models.login_code import LoginCode

router = Router()
settings = get_settings()


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
                f"ID: <code>{tg_id}</code>\n"
                "Передай его админу для привязки к магазину.\n\n"
                "После привязки нажми <b>🌐 Войти на сайт</b> — откроется касса одной кнопкой.",
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
                f"Привет, {user.full_name}!\n"
                f"ID: <code>{tg_id}</code>\n"
                "Админ ещё не привязал тебя к магазину.",
                reply_markup=main_keyboard(),
            )
            return

        store_names = ", ".join(link.Store.name for link in links)
        await message.answer(
            f"Привет, <b>{user.full_name}</b>!\n"
            f"Магазин: <b>{store_names}</b>\n\n"
            "Нажми <b>🌐 Войти на сайт</b> — одна кнопка, без ввода кода.",
            reply_markup=main_keyboard(),
        )


@router.message(F.text == "🌐 Войти на сайт")
@router.message(Command("web"))
async def web_login(message: Message):
    tg_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await message.answer("Сначала /start")
            return

        # антиспам 20 сек
        recent = await session.execute(
            select(LoginCode)
            .where(LoginCode.user_id == user.id)
            .order_by(LoginCode.created_at.desc())
            .limit(1)
        )
        last = recent.scalar_one_or_none()
        if last and last.created_at:
            created = last.created_at if last.created_at.tzinfo else last.created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created < timedelta(seconds=20):
                await message.answer("Подожди пару секунд…")
                return

        old = await session.execute(
            select(LoginCode).where(LoginCode.user_id == user.id, LoginCode.used == False)
        )
        for c in old.scalars().all():
            c.used = True

        token = new_login_token()
        pin = new_login_pin()
        exp = datetime.now(timezone.utc) + timedelta(minutes=3)

        session.add(LoginCode(user_id=user.id, code=token, expires_at=exp, used=False))
        session.add(LoginCode(user_id=user.id, code=pin, expires_at=exp, used=False))
        await session.commit()

    base = (settings.WEB_BASE_URL or "").rstrip("/")
    if base:
        url = f"{base}/auth/magic?t={token}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть кассу", url=url)]
        ])
        await message.answer(
            "Нажми кнопку — сразу попадёшь в кассу.\n"
            f"Ссылка действует <b>3 минуты</b>.\n\n"
            f"Запасной код (если кнопка не откроется): <code>{pin}</code>",
            reply_markup=kb,
        )
    else:
        await message.answer(
            f"🔐 Код входа: <code>{pin}</code>\n"
            f"3 минуты, один раз.\n\n"
            f"<i>Админу: задайте WEB_BASE_URL в Render, чтобы появилась кнопка «Открыть кассу».</i>"
        )


@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "<b>CloudVape</b>\n\n"
        "1. /start\n"
        "2. Админ привязывает к магазину\n"
        "3. «Войти на сайт» → кнопка «Открыть кассу»\n\n"
        "Продажи только на сайте."
    )


@router.message(F.text.in_({
    "🛒 Продажа", "📦 Остатки", "📊 Моя смена", "↩️ Возврат",
    "📋 Инвентаризация", "🌐 Открыть веб-кассу",
}))
async def removed_features(message: Message):
    await message.answer("Используй «🌐 Войти на сайт».")
