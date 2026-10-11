"""Доступ к модели: OpenAI-совместимый API локального llama.cpp server (чат и эмбеддинги)."""

from typing import Any

from pydantic import BaseModel

from app.llm.base import HttpProvider, Message


class OpenAICompatProvider(HttpProvider):
    """OpenAI-совместимый API llama.cpp server: /chat/completions, /embeddings, /models."""

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        base = base_url.rstrip("/")
        super().__init__(base if base.endswith("/v1") else f"{base}/v1", **kwargs)

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        schema: type[BaseModel] | None,
        temperature: float,
    ) -> tuple[str, str]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if self.think is False:
            # vLLM и llama.cpp: выключить рассуждения шаблона Qwen3.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        elif isinstance(self.think, str):
            body["reasoning_effort"] = self.think  # уровень рассуждений для gpt-oss и подобных
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        data = await self.request("POST", "/chat/completions", body)
        return data["choices"][0]["message"]["content"], data.get("model", model)

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        data = await self.request("POST", "/embeddings", {"model": model, "input": texts})
        return [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]

    async def list_models(self) -> list[str]:
        data = await self.request("GET", "/models")
        return [m["id"] for m in data.get("data", [])]


Provider = OpenAICompatProvider
