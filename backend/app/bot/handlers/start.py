from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.filters import CommandStart, Command
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.config import get_settings
from app.core.roles import OWNER, SYSADMIN, SELLER, WAREHOUSE, ADMIN
from app.models.user import User
from app.models.store import StoreUser, Store

router = Router()
settings = get_settings()


def webapp_url(path: str) -> str:
    base = (settings.WEB_BASE_URL or "").rstrip("/")
    return f"{base}{path}"


def keyboard_for(role: str) -> ReplyKeyboardMarkup:
    base = (settings.WEB_BASE_URL or "").rstrip("/")
    if not base:
        # fallback без webapp
        if role in (OWNER, SYSADMIN, ADMIN):
            return ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="ℹ️ Нужен WEB_BASE_URL")], [KeyboardButton(text="ℹ️ Помощь")]],
                resize_keyboard=True,
            )
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="ℹ️ Нужен WEB_BASE_URL")], [KeyboardButton(text="ℹ️ Помощь")]],
            resize_keyboard=True,
        )

    if role in (OWNER, SYSADMIN, ADMIN):
        return ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text="🛒 Касса", web_app=WebAppInfo(url=webapp_url("/app/sell"))),
                    KeyboardButton(text="⚙️ Админка", web_app=WebAppInfo(url=webapp_url("/app/admin"))),
                ],
                [KeyboardButton(text="ℹ️ Помощь")],
            ],
            resize_keyboard=True,
        )
    if role == WAREHOUSE:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📦 Склад", web_app=WebAppInfo(url=webapp_url("/app/warehouse")))],
                [KeyboardButton(text="ℹ️ Помощь")],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Открыть кассу", web_app=WebAppInfo(url=webapp_url("/app/sell")))],
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
                f"Привет, {full_name}!\n"
                f"ID: <code>{tg_id}</code>\n"
                "Передай ID владельцу для привязки к магазину.\n\n"
                "После привязки кнопка <b>Открыть кассу</b> откроет приложение прямо в Telegram.",
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
                "🛒 <b>Касса</b> — продажи внутри Telegram\n"
                "⚙️ <b>Админка</b> — управление\n\n"
                "Просто нажми кнопку — без кодов и браузера.",
                reply_markup=keyboard_for(role),
            )
            return

        if role == WAREHOUSE:
            await message.answer(
                f"Привет, <b>{user.full_name}</b>!\n"
                "Кнопка <b>Склад</b> откроет товары прямо в Telegram.",
                reply_markup=keyboard_for(role),
            )
            return

        links = (await session.execute(
            select(StoreUser, Store).join(Store, Store.id == StoreUser.store_id)
            .where(StoreUser.user_id == user.id, StoreUser.is_active == True)
        )).all()
        if not links:
            await message.answer(
                f"Привет, {user.full_name}!\nID: <code>{tg_id}</code>\n"
                "Ещё не привязан к магазину — напиши владельцу.",
                reply_markup=keyboard_for(SELLER),
            )
            return

        stores = ", ".join(l.Store.name for l in links)
        await message.answer(
            f"Привет, <b>{user.full_name}</b>!\n"
            f"Магазин: <b>{stores}</b>\n\n"
            "Нажми <b>Открыть кассу</b> — приложение откроется здесь же.",
            reply_markup=keyboard_for(SELLER),
        )


@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "<b>CloudVape</b>\n\n"
        "Касса и админка открываются <b>внутри Telegram</b> (Mini App).\n"
        "Один тап по кнопке — без ссылок и кодов.\n\n"
        "Если кнопки нет: владелец должен привязать тебя к магазину.\n"
        "Если «не открывается»: в BotFather укажи Domain = ваш сайт на Render."
    )


@router.message(F.text == "ℹ️ Нужен WEB_BASE_URL")
async def need_url(message: Message):
    await message.answer(
        "Админу: в Render Environment задай\n"
        "<code>WEB_BASE_URL=https://ваш-сервис.onrender.com</code>\n"
        "и в @BotFather → Bot Settings → Domain укажи этот же домен."
    )
