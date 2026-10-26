from aiogram import Router, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
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


def webapp_url(path: str) -> str:
    base = (settings.WEB_BASE_URL or "").rstrip("/")
    return f"{base}{path}"


def keyboard_for(role: str) -> ReplyKeyboardMarkup:
    base = (settings.WEB_BASE_URL or "").rstrip("/")
    if not base:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="ℹ️ Нужен WEB_BASE_URL")]],
            resize_keyboard=True,
        )

    if role in (OWNER, SYSADMIN, ADMIN):
        return ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text="🛒 Касса", web_app=WebAppInfo(url=webapp_url("/app/sell"))),
                    KeyboardButton(text="⚙️ Админка", web_app=WebAppInfo(url=webapp_url("/app/admin"))),
                ],
                [
                    KeyboardButton(text="🔗 Ссылка: касса"),
                    KeyboardButton(text="🔗 Ссылка: админка"),
                ],
                [KeyboardButton(text="ℹ️ Помощь")],
            ],
            resize_keyboard=True,
        )
    if role == WAREHOUSE:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📦 Склад", web_app=WebAppInfo(url=webapp_url("/app/warehouse")))],
                [KeyboardButton(text="🔗 Ссылка: склад")],
                [KeyboardButton(text="ℹ️ Помощь")],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Открыть кассу", web_app=WebAppInfo(url=webapp_url("/app/sell")))],
            [KeyboardButton(text="🔗 Ссылка для входа")],
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
                role=SELLER,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await message.answer(
                f"Привет, {full_name}!\nID: <code>{tg_id}</code>\n"
                "Передай ID владельцу для привязки.",
                reply_markup=keyboard_for(SELLER),
            )
            return

        if full_name and user.full_name != full_name:
            user.full_name = full_name
            await session.commit()

        role = user.role or SELLER
        if role in (OWNER, SYSADMIN, ADMIN):
            await message.answer(
                f"Привет, <b>{user.full_name}</b>!\n\n"
                "🛒 <b>Касса</b> / ⚙️ <b>Админка</b> — внутри Telegram\n"
                "Если Mini App не пускает — жми <b>🔗 Ссылка</b> (запасной вход).",
                reply_markup=keyboard_for(role),
            )
            return

        if role == WAREHOUSE:
            await message.answer(
                f"Привет, <b>{user.full_name}</b>!\nКнопка Склад или запасная ссылка.",
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
            f"Привет, <b>{user.full_name}</b>!\nМагазин: <b>{stores}</b>\n"
            "Жми <b>Открыть кассу</b>. Если не вошло — <b>🔗 Ссылка для входа</b>.",
            reply_markup=keyboard_for(SELLER),
        )


async def send_magic_link(message: Message, destination: str, label: str):
    base = (settings.WEB_BASE_URL or "").rstrip("/")
    if not base:
        await message.answer("WEB_BASE_URL не задан на сервере.")
        return

    tg_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await message.answer("Сначала /start")
            return

        old = await session.execute(
            select(LoginCode).where(LoginCode.user_id == user.id, LoginCode.used == False)
        )
        for c in old.scalars().all():
            c.used = True

        token = new_login_token()
        session.add(LoginCode(
            user_id=user.id,
            code=token,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            used=False,
        ))
        await session.commit()

    url = f"{base}/auth/magic?t={token}&to={destination}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, url=url)]
    ])
    await message.answer(
        f"Запасной вход (5 мин). Нажми кнопку — откроется браузер уже авторизованным.",
        reply_markup=kb,
    )


@router.message(F.text == "🔗 Ссылка для входа")
@router.message(F.text == "🔗 Ссылка: касса")
async def link_sell(message: Message):
    await send_magic_link(message, "sell", "🚀 Открыть кассу")


@router.message(F.text == "🔗 Ссылка: админка")
async def link_admin(message: Message):
    await send_magic_link(message, "admin", "⚙️ Открыть админку")


@router.message(F.text == "🔗 Ссылка: склад")
async def link_wh(message: Message):
    await send_magic_link(message, "warehouse", "📦 Открыть склад")


@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "<b>CloudVape</b>\n\n"
        "Основной вход — кнопки Касса/Админка (Mini App).\n"
        "Если «не удалось войти» — используй <b>🔗 Ссылка</b>.\n\n"
        "Domain в BotFather должен совпадать с сайтом на Render."
    )


@router.message(F.text == "ℹ️ Нужен WEB_BASE_URL")
async def need_url(message: Message):
    await message.answer(
        "В Render Environment:\n<code>WEB_BASE_URL=https://ваш-сервис.onrender.com</code>"
    )
