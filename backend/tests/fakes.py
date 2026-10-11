"""Подмены внешних сервисов для тестов: модель отвечает заранее заданными ответами."""

import asyncio

from pydantic import BaseModel

from app.llm import ChatResult, Message


class FakeLLM:
    """LLM, которая отдаёт ответы по очереди; исключение в очереди — бросается."""

    embed_model = "fake-embed"
    chat_model = "fake-chat"

    def __init__(self, replies: list[BaseModel | Exception] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
    ) -> ChatResult:
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("FakeLLM: ответы кончились, вызов не ожидался")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(
            text=reply.model_dump_json(), model=self.chat_model, latency_ms=1.0, parsed=reply
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("FakeLLM.embed не ожидался")


class FakeRedis:
    """Минимум Redis для хранилищ: строки и хэши с TTL (TTL только запоминается).

    yield_between=True отдаёт управление внутри каждой операции — так видны гонки read-modify-write.
    """

    def __init__(self, yield_between: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.ttl: dict[str, int | None] = {}
        self.yield_between = yield_between

    async def _tick(self) -> None:
        if self.yield_between:
            await asyncio.sleep(0)

    async def get(self, key: str) -> str | None:
        await self._tick()
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self._tick()
        self.data[key] = value
        self.ttl[key] = ex

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: object = None,
        mapping: dict[str, object] | None = None,
    ) -> int:
        h = self.hashes.setdefault(key, {})
        items = dict(mapping or {})
        if field is not None:
            items[field] = value
        added = sum(f not in h for f in items)
        h.update({f: str(v) for f, v in items.items()})
        return added

    async def hsetnx(self, key: str, field: str, value: object) -> int:
        h = self.hashes.setdefault(key, {})
        if field in h:
            return 0
        h[field] = str(value)
        return 1

    async def hget(self, key: str, field: str) -> str | None:
        await self._tick()
        return self.hashes.get(key, {}).get(field)

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        await self._tick()
        h = self.hashes.get(key, {})
        return [h.get(f) for f in fields]

    async def hlen(self, key: str) -> int:
        await self._tick()
        return len(self.hashes.get(key, {}))

    async def hgetall(self, key: str) -> dict[str, str]:
        await self._tick()
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        await self._tick()
        have = self.hashes.get(key, {})
        return sum(have.pop(f, None) is not None for f in fields)

    async def rpush(self, key: str, *values: str) -> int:
        lst = self.lists.setdefault(key, [])
        lst.extend(values)
        return len(lst)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        await self._tick()
        lst = self.lists.get(key, [])
        return lst[start:] if end == -1 else lst[start : end + 1]

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self.data | self.hashes | self.lists | self.sorted_sets:
            return False
        self.ttl[key] = seconds
        return True

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        have = self.sorted_sets.setdefault(key, {})
        added = sum(member not in have for member in mapping)
        have.update(mapping)
        return added

    def _ranked(self, key: str) -> list[str]:
        members = self.sorted_sets.get(key, {})
        return [m for m, _ in sorted(members.items(), key=lambda item: item[1])]

    async def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        await self._tick()
        ranked = self._ranked(key)[::-1]
        return ranked[start:] if end == -1 else ranked[start : end + 1]

    async def zrem(self, key: str, *members: str) -> int:
        have = self.sorted_sets.get(key, {})
        return sum(have.pop(m, None) is not None for m in members)

    async def zremrangebyrank(self, key: str, start: int, end: int) -> int:
        ranked = self._ranked(key)
        n = len(ranked)
        lo = start if start >= 0 else n + start
        hi = end if end >= 0 else n + end
        doomed = ranked[max(lo, 0) : hi + 1] if hi >= 0 else []
        return await self.zrem(key, *doomed)

    async def zcard(self, key: str) -> int:
        await self._tick()
        return len(self.sorted_sets.get(key, {}))

    async def zrangebyscore(
        self,
        key: str,
        minimum: str,
        maximum: str,
        *,
        start: int,
        num: int,
    ) -> list[str]:
        await self._tick()
        after = float(minimum.removeprefix("("))
        return [
            member
            for member, score in sorted(
                self.sorted_sets.get(key, {}).items(), key=lambda item: item[1]
            )
            if score > after
        ][start : start + num]

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)

    def register_script(self, script: str) -> "FakeScript":
        return FakeScript(self, script)

    async def run_script(self, script: str, keys: list[str], args: list[object]) -> object:
        """Скрипты Redis, которые знает фейк: запись результата строки пакета (app.batch)."""
        if "HSETNX" in script and "HINCRBY" in script:
            results, counts = keys[0], keys[1]
            total = self.hashes.get(keys[2], {}).get("total")
            if total is None:
                return -1
            row, value, status, ttl = str(args[0]), str(args[1]), str(args[2]), int(str(args[3]))
            if await self.hsetnx(results, row, value):
                h = self.hashes.setdefault(counts, {})
                h[status] = str(int(h.get(status, "0")) + 1)
                await self.zadd(keys[4], {row: float(row)})
                await self.zadd(keys[int(str(args[5])) - 1], {row: float(row)})
                if self.hashes[keys[2]].get("cancelled"):
                    ttl = self.ttl[keys[2]] or 0
                elif len(self.hashes[results]) >= int(total):
                    ttl = int(str(args[4]))
                for key in keys:
                    await self.expire(key, ttl)
            return len(self.hashes[results])
        if "HSETNX" in script and "'started'" in script:
            meta, _, results, *_ = keys
            total = self.hashes.get(meta, {}).get("total")
            if (
                total is None
                or self.hashes[meta].get("cancelled")
                or len(self.hashes.get(results, {})) >= int(total)
            ):
                return 0
            await self.hsetnx(meta, "started", "1")
            for key in keys:
                await self.expire(key, int(str(args[0])))
            return 1
        if "'cancelled', '1'" in script:
            meta, _, results, *_ = keys
            total = self.hashes.get(meta, {}).get("total")
            if total is None:
                return -1
            if self.hashes[meta].get("cancelled") or len(self.hashes.get(results, {})) >= int(
                total
            ):
                return 0
            await self.hset(meta, "cancelled", "1")
            for key in keys:
                await self.expire(key, int(str(args[0])))
            return 1
        raise NotImplementedError(script)


class FakeScript:
    """Lua-скрипт: выполняется целиком, без передачи управления (как EVALSHA)."""

    def __init__(self, redis: FakeRedis, script: str) -> None:
        self.redis, self.script = redis, script

    async def __call__(self, keys: list[str], args: list[object]) -> object:
        tick, self.redis.yield_between = self.redis.yield_between, False
        try:
            return await self.redis.run_script(self.script, keys, args)
        finally:
            self.redis.yield_between = tick


class FakePipeline:
    """MULTI/EXEC: команды копятся и выполняются подряд, без передачи управления между ними."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.calls: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.calls = []

    def __getattr__(self, name: str):
        def queue(*args: object, **kwargs: object) -> "FakePipeline":
            self.calls.append((name, args, kwargs))
            return self

        return queue

    async def execute(self) -> list[object]:
        tick, self.redis.yield_between = self.redis.yield_between, False
        try:
            return [await getattr(self.redis, n)(*a, **kw) for n, a, kw in self.calls]
        finally:
            self.redis.yield_between = tick
            self.calls = []
