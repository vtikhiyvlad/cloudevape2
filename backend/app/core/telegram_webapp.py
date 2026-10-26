"""Проверка initData Telegram Mini App."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from app.core.config import get_settings


def _calc_hash(data_check_string: str, bot_token: str) -> str:
    # Official: secret_key = HMAC_SHA256(<bot_token>, "WebAppData") with key=WebAppData
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


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
        print("webapp: no hash in initData")
        return None

    check_list = [f"{k}={v}" for k, v in sorted(parsed.items())]
    data_check_string = chr(10).join(check_list)
    calculated = _calc_hash(data_check_string, token)

    if not hmac.compare_digest(calculated, received_hash):
        print("webapp: hash mismatch")
        return None

    auth_date = parsed.get("auth_date")
    if auth_date:
        try:
            ts = int(auth_date)
            age = datetime.now(timezone.utc).timestamp() - ts
            if age > max_age_sec or age < -300:
                print("webapp: auth_date age", age)
                return None
        except ValueError:
            return None

    user_raw = parsed.get("user")
    if user_raw:
        try:
            parsed["user"] = json.loads(user_raw)
        except json.JSONDecodeError:
            print("webapp: bad user json")
            return None

    return parsed
