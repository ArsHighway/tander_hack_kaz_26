"""Шаблон КТРУ и позиция из него с одинаковыми характеристиками — одна, более свежая."""

from pathlib import Path

from app.catalog import CharacteristicSpec, Position
from app.catalog.ktru_meta import KtruMeta, load_meta, merge_same
from app.narrowing import fix_hint

KIND = CharacteristicSpec(
    name="Тип конструкции", kind="qualitative", values=["Прямая", "Угловая"], required=True
)


def _pos(code: str, values: list[str] | None = None) -> Position:
    spec = KIND.model_copy(update={"values": values or KIND.values})
    return Position(
        code=code,
        name="Тумба для уголка живой природы",
        okpd2="31.09.13.190",
        characteristics=[spec],
        source="ktru-xml",
    )


def test_real_template_pair_is_in_the_shipped_meta() -> None:
    meta = load_meta()
    assert meta["31.09.13.190-00000012"].template
    assert meta["31.09.13.190-00000011"].parent == "31.09.13.190-00000012"


def test_identical_positions_become_one_with_an_explanation(monkeypatch) -> None:
    child, template = _pos("31.09.13.190-00000011"), _pos("31.09.13.190-00000012")
    kept, merges = merge_same([child, template])
    assert [p.code for p in kept] == ["31.09.13.190-00000012"]  # внесена позже — свежее
    [m] = merges
    assert m.dropped == ["31.09.13.190-00000011"]
    assert "шаблон" in m.message and "создана из шаблона" in m.message
    assert "более свежая" in m.message


def test_different_lists_are_not_merged() -> None:
    kept, merges = merge_same([_pos("A-1"), _pos("A-2", ["Прямая"])])
    assert len(kept) == 2 and merges == []


def test_newer_update_wins(monkeypatch) -> None:
    meta = {
        "A-1": KtruMeta(template=True, parent=None, version=1, included="2020", updated="2025"),
        "A-2": KtruMeta(template=False, parent="A-1", version=1, included="2020", updated="2024"),
    }
    monkeypatch.setattr("app.catalog.ktru_meta.load_meta", lambda path=Path(): meta)
    kept, [m] = merge_same([_pos("A-2"), _pos("A-1")])
    assert [p.code for p in kept] == ["A-1"]
    assert "обновлена позже" in m.message


def test_hint_names_the_characteristic_and_a_value_to_add() -> None:
    hint = fix_hint([_pos("A-1"), _pos("A-2", ["Прямая"])])
    assert hint is not None
    assert "Тип конструкции" in hint and "Угловая" in hint


def test_conflicting_types_go_last() -> None:
    from app.narrowing import CONFIRM, NOTHING_FITS, KtruState, Option, Question
    from app.pipeline import _conflicts_last
    from app.search import Candidate

    def opt(v: str) -> Option:
        return Option(value=v, remaining=1, cuts=0, codes=[v])

    cands = [
        Candidate(code="A", name="A", score=1, why=[], conflicts=["Материал: глина"]),
        Candidate(code="B", name="B", score=1, why=[]),
        Candidate(code="C", name="C", score=1, why=[]),
    ]
    q = Question(characteristic=CONFIRM, options=[opt("A"), opt("B"), opt("C"), opt(NOTHING_FITS)])
    state = KtruState(candidates=cands, remaining=3, final_code=None, question=q, steps=[])
    from app.session import Session

    session = Session.new(name="Тумбочка", description="")
    _conflicts_last(state, session)
    assert [o.value for o in q.options] == ["B", "C", "A", NOTHING_FITS]
    # Самый вероятный по модели — первым, даже с противоречием («глина» — абсурд, а не довод).
    session.type_order, session.type_reason = ["A", "B"], "для спальни — прикроватная"
    _conflicts_last(state, session)
    assert [o.value for o in q.options] == ["A", "B", "C", NOTHING_FITS]
    assert "Вероятнее всего это «A»" in state.steps[-1]
