from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
import secrets

from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.store import StoreUser, Store
from app.models.login_code import LoginCode

router = Router()


def main_keyboard(has_store: bool = False):
    rows = [
        [KeyboardButton(text="🛒 Продажа")],
        [KeyboardButton(text="📦 Остатки"), KeyboardButton(text="📊 Моя смена")],
        [KeyboardButton(text="🌐 Открыть веб-кассу")],
    ]
    if has_store:
        rows.append([KeyboardButton(text="📋 Инвентаризация")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


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
                "Регистрация через Telegram прошла.\n"
                "Администратор должен привязать тебя к магазину.\n\n"
                f"Твой ID: <code>{tg_id}</code>\n"
                "Передай его админу для привязки.",
                reply_markup=main_keyboard(False),
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
                "Ты ещё не привязан к магазину.\n"
                f"Твой Telegram ID: <code>{tg_id}</code>\n"
                "Передай его администратору.",
                reply_markup=main_keyboard(False),
            )
            return

        store_names = ", ".join(link.Store.name for link in links)
        await message.answer(
            f"Привет, <b>{user.full_name}</b>!\n"
            f"Магазин: <b>{store_names}</b>\n\n"
            "Продажи удобнее в веб-кассе с телефона (камера для штрихкодов).\n"
            "Нажми «🌐 Открыть веб-кассу».",
            reply_markup=main_keyboard(True),
        )


@router.message(F.text == "🌐 Открыть веб-кассу")
@router.message(Command("web"))
async def web_login_code(message: Message):
    """Безопасный одноразовый код + подсказка открыть сайт"""
    tg_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Сначала /start")
            return

        # Антиспам: не чаще 1 кода в 30 сек
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

        # 8-значный криптостойкий код
        code = f"{secrets.randbelow(100_000_000):08d}"
        lc = LoginCode(
            user_id=user.id,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=3),
            used=False,
        )
        session.add(lc)
        await session.commit()

    await message.answer(
        f"🔐 <b>Вход в веб-кассу</b>\n\n"
        f"Код: <code>{code}</code>\n"
        f"Действует <b>3 минуты</b>, один раз.\n\n"
        f"1. Открой сайт CloudVape\n"
        f"2. «Войти через Telegram»\n"
        f"3. Введи код\n\n"
        f"Никому не пересылай код."
    )


@router.message(F.text == "📋 Инвентаризация")
async def inventory_hint(message: Message):
    await message.answer(
        "Инвентаризацию удобнее проходить в веб-версии с камерой.\n"
        "Нажми «🌐 Открыть веб-кассу», войди по коду,\n"
        "затем открой раздел инвентаризации на сайте.\n\n"
        "(Раздел /inventory — скоро в меню после входа.)"
    )
