"""Новые характеристики, которых нет ни в КТРУ, ни на портале: формулировка записывается и дальше
используется та же (устное уточнение организаторов, 09.10.2026).

«Противоскользящая ручка: да» у второго поставщика становится «Ручка противоскользящая», если так
её уже записали: ключ — основы слов имени без порядка. Хранится в Redis (общий для API и воркеров):
хэш имён и хэш счётчиков.
"""

from typing import cast

from redis.asyncio import Redis

from app.search.text import tokens

NAMES_KEY = "newchar:names"
COUNTS_KEY = "newchar:counts"

# ключ → (формулировка, сколько раз встречалась)
NewNames = dict[str, tuple[str, int]]


def new_key(name: str) -> str:
    """Основы слов имени без порядка: «Ручка противоскользящая» и «Противоскользящая ручка»."""
    return " ".join(sorted(set(tokens(name))))


async def load_new_names(redis: Redis) -> NewNames:
    # Клиент создаётся с decode_responses=True (app.resources): ключи и значения — str.
    names = cast(dict[str, str], await redis.hgetall(NAMES_KEY))
    counts = cast(dict[str, str], await redis.hgetall(COUNTS_KEY))
    return {key: (name, int(counts.get(key, "0") or 0)) for key, name in names.items()}


async def remember_new_names(redis: Redis, names: list[str], have: NewNames) -> None:
    """Формулировка записывается при первой встрече, счётчик растёт при каждой."""
    for name in dict.fromkeys(names):
        key = new_key(name)
        if not key:
            continue
        canon, seen = have.get(key, (name, 0))
        if key not in have:
            await redis.hset(NAMES_KEY, key, canon)
        await redis.hset(COUNTS_KEY, key, str(seen + 1))
        have[key] = (canon, seen + 1)
