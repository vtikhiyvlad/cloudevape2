"""Проверка initData Telegram Mini App (официальный алгоритм)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from app.core.config import get_settings


def validate_init_data(init_data: str, max_age_sec: int = 86400) -> dict | None:
    if not init_data or not str(init_data).strip():
        return None

    settings = get_settings()
    token = (settings.BOT_TOKEN or "").strip()
    if not token:
        print("webapp: BOT_TOKEN empty")
        return None

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    check_list = [f"{k}={v}" for k, v in sorted(parsed.items())]
    data_check_string = chr(10).join(check_list)

    # secret_key = HMAC_SHA256(key="WebAppData", data=bot_token)
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    calculated = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        print("webapp: hash mismatch")
        return None

    auth_date = parsed.get("auth_date")
    if auth_date:
        try:
            ts = int(auth_date)
            age = datetime.now(timezone.utc).timestamp() - ts
            if age > max_age_sec or age < -300:
                print("webapp: auth_date expired", age)
                return None
        except ValueError:
            return None

    user_raw = parsed.get("user")
    if user_raw:
        try:
            parsed["user"] = json.loads(user_raw)
        except json.JSONDecodeError:
            return None

    return parsed
