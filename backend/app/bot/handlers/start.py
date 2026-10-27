from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from sqlalchemy import select
from datetime import datetime, timezone, timedelta

from app.core.database import AsyncSessionLocal
from app.core.config import get_settings
from app.core.security import new_login_token
from app.core.roles import OWNER, SYSADMIN, SELLER, WAREHOUSE, ADMIN
from app.models.user import User
from app.models.store import StoreUser, Store
from app.models.login_code import LoginCode

router = Router()
settings = get_settings()


def keyboard_for(role: str) -> ReplyKeyboardMarkup:
    if role in (OWNER, SYSADMIN, ADMIN):
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🛒 Касса"), KeyboardButton(text="⚙️ Админка")],
                [KeyboardButton(text="📦 Склад")],
                [KeyboardButton(text="ℹ️ Помощь")],
            ],
            resize_keyboard=True,
        )
    if role == WAREHOUSE:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📦 Склад"), KeyboardButton(text="🛒 Касса")],
                [KeyboardButton(text="ℹ️ Помощь")],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛒 Касса")], [KeyboardButton(text="ℹ️ Помощь")]],
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
                telegram_id=tg_id, full_name=full_name, username=username,
                role=SELLER, is_active=True,
            )
            session.add(user)
            await session.commit()
            await message.answer(
                f"Привет, {full_name}!\nID: <code>{tg_id}</code>\nПередай ID владельцу.",
                reply_markup=keyboard_for(SELLER),
            )
            return

        if full_name and user.full_name != full_name:
            user.full_name = full_name
            await session.commit()

        role = user.role or SELLER
        if role == WAREHOUSE:
            await message.answer(
                f"Привет, <b>{user.full_name}</b>!\n"
                "📦 <b>Склад</b> — все магазины\n🛒 <b>Касса</b> — продажи",
                reply_markup=keyboard_for(role),
            )
            return
        if role in (OWNER, SYSADMIN, ADMIN):
            await message.answer(
                f"Привет, <b>{user.full_name}</b>!\nКасса / Админка / Склад — одноразовые ссылки.",
                reply_markup=keyboard_for(role),
            )
            return

        links = (await session.execute(
            select(StoreUser, Store).join(Store, Store.id == StoreUser.store_id)
            .where(StoreUser.user_id == user.id, StoreUser.is_active == True)
        )).all()
        if not links:
            await message.answer(
                f"Привет, {user.full_name}!\nID: <code>{tg_id}</code>\nНе привязан к магазину.",
                reply_markup=keyboard_for(SELLER),
            )
            return
        stores = ", ".join(l.Store.name for l in links)
        await message.answer(
            f"Привет, <b>{user.full_name}</b>!\nМагазин: <b>{stores}</b>\nНажми <b>Касса</b>.",
            reply_markup=keyboard_for(SELLER),
        )


async def send_magic_link(message: Message, destination: str, label: str):
    base = (settings.WEB_BASE_URL or "").rstrip("/")
    if not base:
        await message.answer("WEB_BASE_URL не задан.")
        return
    tg_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await message.answer("Сначала /start")
            return
        recent = await session.execute(
            select(LoginCode).where(LoginCode.user_id == user.id)
            .order_by(LoginCode.created_at.desc()).limit(1)
        )
        last = recent.scalar_one_or_none()
        if last and last.created_at:
            created = last.created_at if last.created_at.tzinfo else last.created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created < timedelta(seconds=12):
                await message.answer("Подожди пару секунд…")
                return
        old = await session.execute(
            select(LoginCode).where(LoginCode.user_id == user.id, LoginCode.used == False)
        )
        for c in old.scalars().all():
            c.used = True
        token = new_login_token()
        session.add(LoginCode(
            user_id=user.id, code=token,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), used=False,
        ))
        await session.commit()
    url = f"{base}/auth/magic?t={token}&to={destination}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]])
    await message.answer("Ссылка <b>5 минут</b>, один раз. Никому не пересылай.", reply_markup=kb)


@router.message(F.text == "🛒 Касса")
async def btn_sell(message: Message):
    await send_magic_link(message, "sell", "🚀 Открыть кассу")


@router.message(F.text == "⚙️ Админка")
async def btn_admin(message: Message):
    await send_magic_link(message, "admin", "⚙️ Открыть админку")


@router.message(F.text == "📦 Склад")
async def btn_wh(message: Message):
    await send_magic_link(message, "warehouse", "📦 Открыть склад")


@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("CloudVape — вход по одноразовой ссылке из бота.")
