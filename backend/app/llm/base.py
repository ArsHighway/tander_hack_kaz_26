"""Общая часть клиентов LLM: HTTP с повторами и разбор структурированного ответа кодом."""

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Think

log = logging.getLogger(__name__)

Message = dict[str, str]

RETRY_STATUSES = {500, 502, 503, 504}  # 429 — отдельно, со своим бюджетом и паузой
_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class LLMError(RuntimeError):
    """Сервер модели недоступен или ответил ошибкой."""


class LLMOutputError(LLMError):
    """Модель ответила, но ответ не прошёл проверку схемы."""

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    latency_ms: float
    parsed: BaseModel | None = None


def clean_output(text: str) -> str:
    """Убирает рассуждения <think>…</think> (в том числе незакрытые) и ограду ```json."""
    text = _THINK.sub("", text)
    if "<think>" in text:  # незакрытый блок рассуждений — ответа после него нет
        text = text.split("<think>", 1)[0]
    return _FENCE.sub("", text.strip()).strip()


def parse_structured[T: BaseModel](text: str, schema: type[T]) -> T:
    cleaned = clean_output(text)
    try:
        return schema.model_validate_json(cleaned)
    except ValidationError:
        pass
    # Модель иногда оборачивает JSON текстом: берём первый объект целиком.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return schema.model_validate(json.loads(cleaned[start : end + 1]))
        except (ValueError, ValidationError):
            pass
    raise LLMOutputError(f"ответ не соответствует схеме {schema.__name__}", raw=text)


class LLMRateLimited(LLMError):
    """Сервер так и ответил 429 после всех повторов с паузой (лимит тарифа или сервера)."""


class LLMInputError(LLMError):
    """Сервер работает, но не справился с этим входом (bge-m3 отдаёт NaN на части текстов →
    500). Повтор того же текста бесполезен, а сервис не считается упавшим."""


# Ответ 500 на конкретный текст, а не отказ сервера: повторять нет смысла.
_INPUT_FAILURES = ("unsupported value: NaN",)
# 429 «квота кончилась» («You reached the Free usage limit») за 30 с не
# пройдёт: повторы с паузой только держали запрос минуту.
_QUOTA_MARKERS = ("usage limit", "quota")
# Плохой запрос (контекст переполнен, схема) — беда входа, а не сервиса: без повторов и паузы.
_INPUT_STATUSES = {400, 413, 422}
# Пауза после 429: заголовок Retry-After, иначе 1, 2, 4 … с, не дольше этого потолка.
RATE_LIMIT_MAX_DELAY_S = 30.0


def _short_retry(resp: httpx.Response) -> bool:
    """Retry-After не дольше нашей паузы: «подождите секунду», а не «квота кончилась на сутки»."""
    raw = resp.headers.get("retry-after", "").strip()
    try:
        return bool(raw) and float(raw) <= RATE_LIMIT_MAX_DELAY_S
    except ValueError:
        return False


def retry_after_s(resp: httpx.Response) -> float | None:
    """Retry-After в секундах (дату HTTP не разбираем — тогда своя пауза)."""
    raw = resp.headers.get("retry-after", "").strip()
    try:
        return min(max(float(raw), 0.0), RATE_LIMIT_MAX_DELAY_S) if raw else None
    except ValueError:
        return None


class HttpProvider:
    """HTTP-клиент провайдера с повторами на сетевых ошибках и 5xx; у 429 свой бюджет повторов.

    429 (облачный тариф, перегруженный сервер) — не сбой, а «подождите»: ждём Retry-After или
    растущую паузу и повторяем до `rate_limit_retries` раз, не тратя повторы сетевых ошибок.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 2,
        rate_limit_retries: int = 6,
        think: Think = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.think = think
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.rate_limit_retries = rate_limit_retries
        self._http = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )

    async def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        last: Exception | None = None
        attempt = limited = 0
        while True:
            try:
                resp = await self._http.request(method, path, json=payload)
            except httpx.TimeoutException as exc:
                if not isinstance(exc, httpx.ConnectTimeout):
                    # Долгая генерация: повтор съест ещё столько же времени, отдаём ошибку сразу.
                    raise LLMError(f"{method} {self.base_url}{path}: {type(exc).__name__}") from exc
                last = exc
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code < 400:
                    return resp.json()
                detail = f"{method} {path} → {resp.status_code}: {resp.text[:300]}"
                if resp.status_code == 429:
                    # «Квота кончилась» без короткого Retry-After — не ждём: поминутная квота
                    # («check quota», Retry-After 1 с) повторяется как обычный 429.
                    quota = any(m in resp.text.casefold() for m in _QUOTA_MARKERS)
                    if (quota and not _short_retry(resp)) or limited >= self.rate_limit_retries:
                        raise LLMRateLimited(detail)
                    delay = retry_after_s(resp)
                    if delay is None:
                        delay = min(RATE_LIMIT_MAX_DELAY_S, 2.0**limited)
                    delay += random.uniform(0, 0.5)  # разводим одновременных клиентов
                    limited += 1
                    log.warning("LLM %s %s: 429, пауза %.1f с (%d)", method, path, delay, limited)
                    await asyncio.sleep(delay)
                    continue
                if any(marker in resp.text for marker in _INPUT_FAILURES):
                    raise LLMInputError(detail)
                if resp.status_code in _INPUT_STATUSES:
                    # Сервер работает, вход не подошёл (переполнен контекст, не та схема).
                    raise LLMInputError(detail)
                last = LLMError(detail)
                if resp.status_code not in RETRY_STATUSES:
                    raise last
            if attempt >= self.max_retries:
                break
            delay = 0.5 * 2**attempt
            attempt += 1
            log.warning("LLM %s %s: %s, повтор через %.1f с", method, path, last, delay)
            await asyncio.sleep(delay)
        if isinstance(last, LLMError):
            raise last
        raise LLMError(f"{method} {self.base_url}{path}: {type(last).__name__}: {last}") from last

    async def aclose(self) -> None:
        await self._http.aclose()
