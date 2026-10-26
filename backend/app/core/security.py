"""Хеши паролей и безопасные сравнения."""
from passlib.context import CryptContext
import secrets
import hmac

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    # bcrypt лимит 72 байта
    if len(password.encode("utf-8")) > 72:
        password = password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        if len(plain.encode("utf-8")) > 72:
            plain = plain.encode("utf-8")[:72].decode("utf-8", errors="ignore")
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def new_login_token() -> str:
    """Криптостойкий токен для magic-ссылки."""
    return secrets.token_urlsafe(32)


def new_login_pin() -> str:
    """8-значный PIN (fallback)."""
    return f"{secrets.randbelow(100_000_000):08d}"
