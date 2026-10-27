"""Уведомления владельцу в Telegram. Фото только из памяти, без записи на диск."""
from __future__ import annotations

import json
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.models.user import User


async def send_telegram_message(chat_id: int, text: str) -> bool:
    settings = get_settings()
    if not settings.BOT_TOKEN or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
            return r.status_code == 200
    except Exception as e:
        print("notify error:", e)
        return False


async def send_telegram_photos(
    chat_id: int,
    caption: str,
    files: list[tuple[str, bytes, str]],
) -> bool:
    """Отправка 1–10 фото из bytes (без файлов на диске)."""
    settings = get_settings()
    if not settings.BOT_TOKEN or not chat_id or not files:
        return False
    token = settings.BOT_TOKEN
    files = files[:10]
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            if len(files) == 1:
                name, content, mime = files[0]
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={
                        "chat_id": str(chat_id),
                        "caption": (caption or "")[:1024],
                        "parse_mode": "HTML",
                    },
                    files={"photo": (name or "photo.jpg", content, mime or "image/jpeg")},
                )
                if r.status_code != 200:
                    print("sendPhoto fail:", r.status_code, r.text[:300])
                return r.status_code == 200

            media = []
            form_files = {}
            for i, (name, content, mime) in enumerate(files):
                key = f"photo{i}"
                item: dict = {"type": "photo", "media": f"attach://{key}"}
                if i == 0:
                    item["caption"] = (caption or "")[:1024]
                    item["parse_mode"] = "HTML"
                media.append(item)
                form_files[key] = (name or f"photo{i}.jpg", content, mime or "image/jpeg")

            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMediaGroup",
                data={"chat_id": str(chat_id), "media": json.dumps(media)},
                files=form_files,
            )
            if r.status_code != 200:
                print("sendMediaGroup fail:", r.status_code, r.text[:400])
                # fallback: по одному
                ok_any = False
                for i, (name, content, mime) in enumerate(files):
                    cap = caption if i == 0 else ""
                    rr = await client.post(
                        f"https://api.telegram.org/bot{token}/sendPhoto",
                        data={"chat_id": str(chat_id), "caption": (cap or "")[:1024], "parse_mode": "HTML"},
                        files={"photo": (name or "photo.jpg", content, mime or "image/jpeg")},
                    )
                    if rr.status_code == 200:
                        ok_any = True
                return ok_any
            return True
    except Exception as e:
        print("notify photos error:", e)
        return False


async def notify_owners(
    db: AsyncSession,
    text: str,
    photo_files: list[tuple[str, bytes, str]] | None = None,
) -> int:
    result = await db.execute(
        select(User).where(
            User.is_active == True,
            User.role.in_(("owner", "sysadmin")),
            User.telegram_id.isnot(None),
        )
    )
    n = 0
    owners = list(result.scalars().all())
    for u in owners:
        ok = False
        if photo_files:
            ok = await send_telegram_photos(u.telegram_id, text, photo_files)
            if not ok:
                ok = await send_telegram_message(
                    u.telegram_id,
                    text + "\n\n<i>(фото не удалось отправить)</i>",
                )
        else:
            ok = await send_telegram_message(u.telegram_id, text)
        if ok:
            n += 1
    return n
