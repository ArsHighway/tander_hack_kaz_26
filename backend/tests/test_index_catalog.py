import asyncio

from app.catalog import CharacteristicSpec, Position
from app.llm import LLMInputError
from app.tools.index_catalog import embed_positions


def pos(code: str, name: str) -> Position:
    spec = CharacteristicSpec(name="Цвет", kind="qualitative", values=["синий"])
    return Position(code=code, name=name, okpd2="", characteristics=[spec], source="t")


def test_embed_positions_survives_texts_the_model_cannot_encode() -> None:
    """bge-m3 отдаёт NaN (500) на части текстов: падает вся пачка, а не одна позиция."""
    calls: list[list[str]] = []

    async def embed(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        if any("Плохая" in t and "Цвет" in t for t in texts):  # полный текст не кодируется
            raise LLMInputError("NaN")
        if any("Безнадёжная" in t for t in texts):
            raise LLMInputError("NaN")
        return [[float(len(t))] for t in texts]

    positions = [pos("1", "Хорошая"), pos("2", "Плохая"), pos("3", "Безнадёжная")]
    rows, skipped = asyncio.run(embed_positions(positions, embed, batch=8))
    assert [code for code, _ in rows] == ["1", "2"]  # «Плохая» — по одному наименованию
    assert rows[1][1] == [float(len("Плохая"))]
    assert skipped == ["3"]
