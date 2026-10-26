"""Уведомления владельцу/админу в Telegram"""
import httpx
from app.core.config import get_settings


async def send_telegram_message(chat_id: int, text: str) -> bool:
    settings = get_settings()
    if not settings.BOT_TOKEN or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
            return r.status_code == 200
    except Exception as e:
        print("notify error:", e)
        return False


async def notify_low_stock(owner_telegram_id: int, store_name: str, product_label: str, stock: int):
    await send_telegram_message(
        owner_telegram_id,
        f"⚠️ <b>Мало товара</b>\nМагазин: {store_name}\n{product_label}\nОстаток: <b>{stock}</b> шт.",
    )


async def notify_shift_closed(owner_telegram_id: int, seller_name: str, store_name: str, total: float, count: int):
    await send_telegram_message(
        owner_telegram_id,
        f"📊 <b>Смена</b>\n{seller_name} · {store_name}\nЧеков: {count}\nСумма: <b>{int(total)} ₽</b>",
    )
