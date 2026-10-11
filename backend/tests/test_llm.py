import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import (
    LLMClient,
    LLMError,
    LLMInputError,
    LLMOutputError,
    LLMRateLimited,
    clean_output,
    model_present,
)


class Extracted(BaseModel):
    name: str
    value: float
    unit: str


def run(coro):
    return asyncio.run(coro)


def chat_ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def client_for(handler, **overrides) -> LLMClient:
    settings = Settings(**{"llm_max_retries": 2, **overrides})
    return LLMClient.from_settings(settings, transport=httpx.MockTransport(handler))


def test_chat_strips_think_and_json_fence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        fenced = '```json\n{"name": "Толщина", "value": 50, "unit": "мкм"}\n```'
        return chat_ok("<think>думаю</think>" + fenced)

    llm = client_for(handler, llm_base_url="http://llm:8080/v1")
    result = run(llm.chat([{"role": "user", "content": "..."}], schema=Extracted))
    assert result.parsed == Extracted(name="Толщина", value=50, unit="мкм")


def test_openai_compat_uses_v1_response_format_and_bearer() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        answer = 'Вот: {"name": "Объём", "value": 35, "unit": "л"}'
        return chat_ok(answer)

    llm = client_for(
        handler, llm_provider="openai", llm_base_url="http://gpu:8000", llm_api_key="secret"
    )
    result = run(llm.chat([{"role": "user", "content": "..."}], schema=Extracted))

    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "Extracted"
    assert result.parsed == Extracted(name="Объём", value=35, unit="л")


def test_invalid_structured_answer_raises_with_raw_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_ok("не знаю")

    llm = client_for(handler)
    with pytest.raises(LLMOutputError) as err:
        run(llm.chat([{"role": "user", "content": "..."}], schema=Extracted))
    assert err.value.raw == "не знаю"


def test_retries_on_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="loading model")
        return chat_ok("привет")

    result = run(client_for(handler).chat([{"role": "user", "content": "hi"}]))
    assert result.text == "привет"
    assert len(calls) == 3


def test_client_error_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, text='model "x" not found')

    with pytest.raises(LLMError, match="404"):
        run(client_for(handler).chat([{"role": "user", "content": "hi"}]))
    assert len(calls) == 1


def test_embeddings_from_separate_openai_server_keep_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "emb"
        assert request.url.path == "/v1/embeddings"
        data = [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]
        return httpx.Response(200, json={"data": data})

    llm = client_for(
        handler, llm_provider="openai", embed_provider="openai", embed_base_url="http://emb/v1"
    )
    assert llm.embed_provider is not llm.chat_provider
    assert run(llm.embed(["a", "b"])) == [[1.0, 0.0], [0.0, 1.0]]


def test_missing_models_accepts_latest_tag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "bge-m3:latest"}]})

    llm = client_for(handler, llm_chat_model="gemma-4-31b-it-Q8_0", embed_model="bge-m3")
    assert run(llm.missing_models()) == ["gemma-4-31b-it-Q8_0"]
    assert model_present("bge-m3", ["bge-m3:latest"])


def test_clean_output_drops_unclosed_think() -> None:
    assert clean_output("ответ") == "ответ"
    assert clean_output("<think>рассуждаю без конца") == ""


async def _no_sleep(_: float) -> None:
    return None


def test_think_off_is_sent_as_template_kwarg() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return chat_ok("ok")

    run(client_for(handler).chat([{"role": "user", "content": "x"}]))
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_think_level_is_sent_as_reasoning_effort() -> None:
    # gpt-oss не выключает рассуждения совсем, но с уровнем low отвечает в разы короче.
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return chat_ok("ok")

    run(client_for(handler, llm_think="low").chat([{"role": "user", "content": "x"}]))
    assert bodies[0]["reasoning_effort"] == "low"
    assert "chat_template_kwargs" not in bodies[0]


def test_think_level_is_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_THINK", "low")
    assert Settings(_env_file=None).llm_think == "low"
    monkeypatch.setenv("LLM_THINK", "false")
    assert Settings(_env_file=None).llm_think is False


def test_read_timeout_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow generation", request=request)

    with pytest.raises(LLMError, match="ReadTimeout"):
        run(client_for(handler).chat([{"role": "user", "content": "x"}]))
    assert len(calls) == 1


# --- лимиты облака: 429, вход, который сервер не переваривает, одновременные запросы ---


def test_429_waits_retry_after_with_its_own_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def record(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= 3:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="rate limited")
        return chat_ok("привет")

    # Повторов сетевых ошибок ноль, а 429 всё равно ждём: у него свой бюджет.
    llm = client_for(handler, llm_max_retries=0)
    assert run(llm.chat([{"role": "user", "content": "hi"}])).text == "привет"
    assert len(calls) == 4
    assert all(2.0 <= d <= 2.5 for d in slept) and len(slept) == 3


def test_429_after_budget_is_rate_limited_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        # временный 429 (перегрузка): повторы с паузой; «квота кончилась» — test_llm_budget.py
        return httpx.Response(429, text="too many requests")

    llm = client_for(handler, llm_rate_limit_retries=3)
    with pytest.raises(LLMRateLimited):
        run(llm.chat([{"role": "user", "content": "hi"}]))
    assert len(calls) == 4


def test_nan_embedding_is_input_error_without_retries() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            500, json={"error": "failed to encode response: json: unsupported value: NaN"}
        )

    with pytest.raises(LLMInputError):
        run(client_for(handler).embed(["текст"]))
    assert len(calls) == 1


def test_chat_concurrency_is_limited_per_process() -> None:
    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return chat_ok("ok")

    llm = client_for(handler, llm_max_concurrency=2)

    async def many() -> None:
        await asyncio.gather(*(llm.chat([{"role": "user", "content": str(i)}]) for i in range(6)))

    run(many())
    assert peak == 2


def test_input_error_does_not_pause_embeddings() -> None:
    from app.pipeline import _embedder

    class Embeds:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise self.exc

    deps = SimpleNamespace(llm=Embeds(LLMInputError("NaN")), embed_paused_until=0.0)
    with pytest.raises(LLMInputError):
        run(_embedder(deps)("текст"))  # type: ignore[arg-type]
    assert deps.embed_paused_until == 0.0
    deps.llm = Embeds(LLMError("connect"))
    with pytest.raises(LLMError):
        run(_embedder(deps)("текст"))  # type: ignore[arg-type]
    assert deps.embed_paused_until > 0.0  # настоящий отказ сервера — пауза
