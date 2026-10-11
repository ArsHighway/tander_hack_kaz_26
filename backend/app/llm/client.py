"""Единая точка входа к моделям: чат и эмбеддинги, провайдер выбирается настройками."""

import asyncio
import contextlib
import time
from typing import Literal, overload

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.llm.base import ChatResult, Message, parse_structured
from app.llm.providers import OpenAICompatProvider, Provider


def make_provider(
    kind: Literal["openai"],
    base_url: str,
    *,
    api_key: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Provider:
    return OpenAICompatProvider(
        base_url,
        api_key=api_key,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        rate_limit_retries=settings.llm_rate_limit_retries,
        think=settings.llm_think,
        transport=transport,
    )


def model_present(wanted: str, available: list[str]) -> bool:
    """Имя модели в списке сервера; тег `:latest` у имени не обязателен."""
    names = set(available)
    return wanted in names or f"{wanted}:latest" in names or wanted.removesuffix(":latest") in names


class LLMClient:
    def __init__(
        self,
        chat_provider: Provider,
        embed_provider: Provider,
        *,
        chat_model: str,
        embed_model: str,
        max_concurrency: int = 0,
    ) -> None:
        self.chat_provider = chat_provider
        self.embed_provider = embed_provider
        self.chat_model = chat_model
        self.embed_model = embed_model
        # Сколько запросов к чат-модели процесс держит одновременно (0 — без лимита). Лимит на
        # процесс: N воркеров дают до N × лимит запросов. Ждущий 429 держит место — так все
        # сопрограммы процесса притормаживают вместе, а не добивают тариф новыми запросами.
        self._slots = asyncio.Semaphore(max_concurrency) if max_concurrency > 0 else None

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> "LLMClient":
        chat = make_provider(
            settings.llm_provider,
            settings.llm_base_url,
            api_key=settings.llm_api_key,
            settings=settings,
            transport=transport,
        )
        embed_kind = settings.embed_provider or settings.llm_provider
        embed_url = settings.embed_base_url or settings.llm_base_url
        embed_key = settings.embed_api_key or settings.llm_api_key
        same = embed_kind == settings.llm_provider and embed_url == settings.llm_base_url
        embed = (
            chat
            if same
            else make_provider(
                embed_kind, embed_url, api_key=embed_key, settings=settings, transport=transport
            )
        )
        return cls(
            chat,
            embed,
            chat_model=settings.llm_chat_model,
            embed_model=settings.embed_model,
            max_concurrency=settings.llm_max_concurrency,
        )

    @overload
    async def chat(
        self, messages: list[Message], *, schema: None = None, temperature: float = 0.0
    ) -> ChatResult: ...

    @overload
    async def chat[T: BaseModel](
        self, messages: list[Message], *, schema: type[T], temperature: float = 0.0
    ) -> ChatResult: ...

    async def chat(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
    ) -> ChatResult:
        """Чат; со схемой ответ валидируется кодом, иначе LLMOutputError (промпт не гарантия)."""
        started = time.perf_counter()
        async with self._slots or contextlib.nullcontext():
            text, model = await self.chat_provider.chat(
                messages, model=self.chat_model, schema=schema, temperature=temperature
            )
        parsed = parse_structured(text, schema) if schema is not None else None
        latency = round((time.perf_counter() - started) * 1000, 1)
        return ChatResult(text=text, model=model, latency_ms=latency, parsed=parsed)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = await self.embed_provider.embed(texts, model=self.embed_model)
        if len(vectors) != len(texts):
            raise ValueError(f"эмбеддингов {len(vectors)}, а текстов {len(texts)}")
        return vectors

    async def missing_models(self) -> list[str]:
        """Какие из настроенных моделей сервер не знает (пусто — всё на месте)."""
        missing = []
        chat_models = await self.chat_provider.list_models()
        if not model_present(self.chat_model, chat_models):
            missing.append(self.chat_model)
        embed_models = (
            chat_models
            if self.embed_provider is self.chat_provider
            else await self.embed_provider.list_models()
        )
        if not model_present(self.embed_model, embed_models):
            missing.append(self.embed_model)
        return missing

    async def aclose(self) -> None:
        await self.chat_provider.aclose()
        if self.embed_provider is not self.chat_provider:
            await self.embed_provider.aclose()
