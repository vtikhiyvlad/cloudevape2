"""Простой in-memory rate limit (одного воркера на Render хватает)."""
from collections import defaultdict
from time import time
import threading

_lock = threading.Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def is_allowed(key: str, max_hits: int = 10, window_sec: int = 60) -> bool:
    now = time()
    with _lock:
        window = [t for t in _hits[key] if now - t < window_sec]
        if len(window) >= max_hits:
            _hits[key] = window
            return False
        window.append(now)
        _hits[key] = window
        return True
