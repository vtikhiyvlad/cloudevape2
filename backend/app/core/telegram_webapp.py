"""Проверка initData Telegram Mini App."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from app.core.config import get_settings


def validate_init_data(init_data: str, max_age_sec: int = 86400) -> dict | None:
    """
    Возвращает parsed data (с user) или None если подпись/срок невалидны.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not init_data.strip():
        return None

    settings = get_settings()
    token = settings.BOT_TOKEN
    if not token:
        return None

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    # data-check-string
    check_list = [f"{k}={v}" for k, v in sorted(parsed.items())]
    data_check_string = "\n".join(check_list)

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
        return None

    auth_date = parsed.get("auth_date")
    if auth_date:
        try:
            ts = int(auth_date)
            age = datetime.now(timezone.utc).timestamp() - ts
            if age > max_age_sec or age < -60:
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
