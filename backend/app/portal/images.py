"""Картинки портала через свой прокси: фото СТЕ и картинка категории с дисковым кэшем.

Фото похожих СТЕ — подсказка у варианта категории в мастере; картинка категории — у карточки без
своего фото (так делает сам портал). Браузер ходит только к модулю: портал может быть недоступен в
день показа, поэтому всё скачанное лежит на диске, а отказ портала — это 404 за таймаут, не ошибка.

Фото СТЕ: `GetSku` → `images[0].fileStorageId`, иначе `productionDirectory.defaultImageId`
(картинка категории) → `Core/Thumbnail/<id>/300/300`. Определённое «фото нет» запоминается
файлом `sku-<id>.none`; сбой и таймаут — нет: в следующий раз портал спросят снова.

Браузеру картинка отдаётся через `soon`: с диска сразу, иначе модуль ждёт портал не дольше `wait_s`,
а скачивание продолжается в фоне и кладёт картинку в кэш к следующему показу. GetSku отвечал 6–27 с
(07.10.2026): синхронное ожидание держало бы соединения браузера, на которых идут ответы мастера.
"""

import asyncio
import logging
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

PORTAL_AGENT = "TenderHackKazanModule/1.0 (+https://zakupki.mos.ru) Mozilla/5.0 (compatible)"
GETSKU_URL = "https://zakupki.mos.ru/classifier/api/Sku/GetSku"
THUMB_URL = "https://zakupki.mos.ru/newapi/api/Core/Thumbnail/{id}/300/300"


def portal_client(timeout_s: float) -> httpx.AsyncClient:
    """Клиент к порталу со своим таймаутом: умолчание httpx — 5 с, и GetSku (6–27 с) обрывался бы
    раньше `timeout_s` при любом внешнем ожидании."""
    return httpx.AsyncClient(
        headers={"User-Agent": PORTAL_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s),
    )


def photo_id(sku: dict[str, Any]) -> int | None:
    """id картинки СТЕ: своё первое фото, иначе картинка категории; нет обоих — None."""
    for image in sku.get("images") or []:
        if isinstance(image, dict) and isinstance(image.get("fileStorageId"), int):
            return int(image["fileStorageId"])
    default = (sku.get("productionDirectory") or {}).get("defaultImageId")
    return int(default) if isinstance(default, int) else None


class ImageCache:
    def __init__(
        self,
        root: Path,
        client: httpx.AsyncClient,
        timeout_s: float = 5.0,
        wait_s: float = 1.0,
        background: int = 4,
        queue: int = 64,
    ) -> None:
        self.root = root
        self.client = client
        self.timeout_s = timeout_s
        self.wait_s = wait_s
        # Фоновые скачивания: одна картинка — одна задача, к порталу не больше `background` сразу.
        self._inflight: dict[str, asyncio.Task[bytes | None]] = {}
        self._slots = asyncio.Semaphore(background)
        # Предел очереди: перебор id не копит запросы к порталу, а картинки текущего экрана не ждут
        # за скачиваниями прошлых.
        self.queue = queue

    def _write(self, name: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{name}.part"
        tmp.write_bytes(data)
        tmp.replace(self.root / name)  # читатель видит файл целиком или не видит вовсе

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response | None:
        try:
            async with asyncio.timeout(self.timeout_s):
                return await self.client.get(url, params=params)
        except (TimeoutError, httpx.HTTPError) as e:
            log.info("портал не ответил: %s %r", url, e)  # у ReadTimeout пустой str()
            return None

    async def _thumb(self, image_id: int, name: str) -> bytes | None:
        r = await self._get(THUMB_URL.format(id=image_id))
        if r is None or r.status_code != httpx.codes.OK:
            return None
        if not r.headers.get("content-type", "").startswith("image/"):
            return None
        self._write(name, r.content)
        return r.content

    async def sku(self, sku_id: int) -> bytes | None:
        """Миниатюра СТЕ (300×300) или None: фото нет, портал недоступен или не ответил вовремя."""
        cached, none = self.root / f"sku-{sku_id}.jpg", self.root / f"sku-{sku_id}.none"
        if cached.is_file():
            return cached.read_bytes()
        if none.is_file():
            return None
        r = await self._get(GETSKU_URL, {"id": sku_id, "withRegionPriceStatistics": "false"})
        if r is None or r.status_code != httpx.codes.OK:
            return None
        try:
            image = photo_id(r.json())
        except ValueError:
            return None
        if image is None:
            self._write(none.name, b"")
            return None
        return await self._thumb(image, cached.name)

    async def _bounded(self, fetch: Awaitable[bytes | None]) -> bytes | None:
        async with self._slots:
            return await fetch

    async def soon(self, kind: Literal["sku", "category"], key: int) -> bytes | None:
        """Для браузера: с диска сразу; иначе не дольше `wait_s`, скачивание продолжится в фоне."""
        name = f"{kind}-{key}"
        cached = self.root / f"{name}.jpg"
        if cached.is_file():
            return cached.read_bytes()
        if (self.root / f"{name}.none").is_file():
            return None
        task = self._inflight.get(name)
        if task is None and len(self._inflight) >= self.queue:
            return None  # очередь полна: 404 сейчас, при следующем показе — снова
        if task is None:
            fetch = self.sku(key) if kind == "sku" else self.category(key)
            task = asyncio.create_task(self._bounded(fetch))
            self._inflight[name] = task
            task.add_done_callback(lambda _: self._inflight.pop(name, None))
        try:
            # shield: ушедший браузер или истёкшее ожидание не отменяют скачивание
            return await asyncio.wait_for(asyncio.shield(task), self.wait_s)
        except TimeoutError:
            return None

    async def close(self) -> None:
        """Остановка процесса: фоновые скачивания отменяются, клиент закрывается."""
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        await self.client.aclose()

    async def category(self, image_id: int) -> bytes | None:
        """Картинка категории (300×300) по id файла (`Position.image_id`) или None."""
        cached = self.root / f"category-{image_id}.jpg"
        if cached.is_file():
            return cached.read_bytes()
        return await self._thumb(image_id, cached.name)
