"""Limites d'essais (connexion). Redis si disponible, sinon compteur en memoire."""
import threading
import time

import redis

from app.core import cache
from app.core.errors import TooManyAttempts

PREFIX = cache.PREFIX + "limit:"
_lock = threading.Lock()
_local: dict[str, tuple[int, float]] = {}


def _local_get(key: str) -> tuple[int, int]:
    count, until = _local.get(key, (0, 0.0))
    left = until - time.monotonic()
    if left <= 0:
        _local.pop(key, None)
        return 0, 0
    return count, int(left) + 1


def check(key: str, maximum: int) -> None:
    """Leve TooManyAttempts si le quota d'echecs de cette cle est atteint."""
    r = cache.client()
    if r is not None:
        try:
            count = r.get(PREFIX + key)
            if count is not None and int(count) >= maximum:
                raise TooManyAttempts(max(r.ttl(PREFIX + key), 1))
            return
        except redis.RedisError:
            pass
    with _lock:
        count, left = _local_get(key)
    if count >= maximum:
        raise TooManyAttempts(left)


def fail(key: str, window_seconds: int) -> None:
    """Compte un echec ; la fenetre repart a chaque echec."""
    r = cache.client()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incr(PREFIX + key)
            pipe.expire(PREFIX + key, window_seconds)
            pipe.execute()
            return
        except redis.RedisError:
            pass
    with _lock:
        count, _ = _local_get(key)
        _local[key] = (count + 1, time.monotonic() + window_seconds)


def reset(key: str) -> None:
    r = cache.client()
    if r is not None:
        try:
            r.delete(PREFIX + key)
        except redis.RedisError:
            pass
    with _lock:
        _local.pop(key, None)


def clear_local() -> None:
    """Tests."""
    with _lock:
        _local.clear()
