"""Шаблон КТРУ и позиция из него с более узким перечнем: берётся позиция, когда текст сузил."""

from app.catalog import CharacteristicSpec, Position, ktru_meta
from app.catalog.ktru_meta import KtruMeta, prefer_refined


def _pos(code: str, power: list[str]) -> Position:
    return Position(
        code=code,
        name="Шуруповерт",
        okpd2="28.24.11.000",
        source="ktru-xml",
        characteristics=[
            CharacteristicSpec(name="Тип питания", kind="qualitative", values=power, required=True)
        ],
    )


TEMPLATE = _pos("T", ["От сети", "От аккумуляторных батарей"])
CHILD = _pos("C", ["От аккумуляторных батарей"])


def _meta(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    meta = {
        "T": KtruMeta(template=True, parent=None, version=1, included=None, updated=None),
        "C": KtruMeta(template=False, parent="T", version=1, included=None, updated=None),
    }
    monkeypatch.setattr(ktru_meta, "load_meta", lambda: meta)


def test_child_wins_when_text_narrowed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _meta(monkeypatch)
    kept, [merge] = prefer_refined([TEMPLATE, CHILD], {"тип питания"})
    assert [p.code for p in kept] == ["C"] and merge.dropped == ["T"]


def test_template_stays_when_text_is_silent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _meta(monkeypatch)
    kept, merges = prefer_refined([TEMPLATE, CHILD], set())
    assert [p.code for p in kept] == ["T", "C"] and merges == []
