"""Одинаковые позиции КТРУ: шаблон и позиция из него, — одна, более свежая, и об этом сказано.

В КТРУ есть позиции с одним наименованием и одинаковыми характеристиками — шаблон (`isTemplate`)
и позиция, созданная из него (`parentPositionInfo`): «Тумба для уголка живой природы»
31.09.13.190-00000012 и …-00000011. Текст поставщика их различить не может, поэтому вопрос «какая
из двух» бессмыслен. Остаётся более свежая (дата обновления, версия, дата включения в КТРУ),
а поставщик видит, какие позиции объединены и почему взята эта (`KtruMerge`).
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.catalog.model import Position
from app.values import fold

META_PATH = Path(__file__).with_name("ktru_meta.json")


@dataclass(frozen=True)
class KtruMeta:
    template: bool
    parent: str | None
    version: int
    included: str | None
    updated: str | None


class KtruMerge(BaseModel):
    """Одинаковые позиции КТРУ, из которых оставлена одна."""

    kept: str = Field(description="код, который остался в выборе")
    dropped: list[str] = Field(description="коды с теми же наименованием и характеристиками")
    name: str
    message: str = Field(description="что объединено и почему взята эта позиция — для человека")


@lru_cache(maxsize=1)
def load_meta(path: Path = META_PATH) -> dict[str, KtruMeta]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {code: KtruMeta(**m) for code, m in raw.items()}


def _signature(p: Position) -> tuple[object, ...]:
    chars = sorted(
        (fold(c.name), c.unit or "", c.required, tuple(sorted(fold(v) for v in c.values)))
        for c in p.characteristics
    )
    return fold(p.name), p.unit or "", tuple(chars)


def _date(stamp: str | None) -> str:
    """«2020-09-19T18:32:04+03:00» → «19.09.2020 18:32»."""
    if not stamp or len(stamp) < 16:
        return "дата неизвестна"
    return f"{stamp[8:10]}.{stamp[5:7]}.{stamp[:4]} {stamp[11:16]}"


def _role(code: str, meta: dict[str, KtruMeta]) -> str:
    m = meta.get(code)
    if m is None:
        return code
    if m.template:
        return f"{code} — шаблон"
    if m.parent:
        return f"{code} — создана из шаблона {m.parent}"
    return code


def _freshness(p: Position, meta: dict[str, KtruMeta]) -> tuple[str, int, str]:
    m = meta.get(p.code)
    if m is None:
        return "", 0, ""
    return m.updated or "", m.version, m.included or ""


def _why(kept: Position, others: list[Position], meta: dict[str, KtruMeta]) -> str:
    """Почему взята эта: что у неё свежее, чем у остальных."""
    mine = meta.get(kept.code)
    if mine is None:
        return "сведений о версиях нет, взята первая"
    rest = [m for p in others if (m := meta.get(p.code)) is not None]
    if any((mine.updated or "") > (m.updated or "") for m in rest):
        return f"обновлена позже ({_date(mine.updated)})"
    if any(mine.version > m.version for m in rest):
        return f"версия новее ({mine.version})"
    if any((mine.included or "") > (m.included or "") for m in rest):
        return f"внесена в КТРУ позже ({_date(mine.included)})"
    return "даты и версии совпадают, взята первая"


def merge_same(positions: list[Position]) -> tuple[list[Position], list[KtruMerge]]:
    """Позиции с одинаковыми наименованием, единицей и характеристиками — одна, более свежая."""
    groups: dict[tuple[object, ...], list[Position]] = {}
    for p in positions:
        groups.setdefault(_signature(p), []).append(p)
    if all(len(g) == 1 for g in groups.values()):
        return positions, []
    meta = load_meta()
    keep: set[str] = set()
    merges: list[KtruMerge] = []
    for group in groups.values():
        kept = max(group, key=lambda p: _freshness(p, meta)) if len(group) > 1 else group[0]
        keep.add(kept.code)
        if len(group) == 1:
            continue
        others = [p for p in group if p.code != kept.code]
        roles = "; ".join(_role(p.code, meta) for p in group)
        merges.append(
            KtruMerge(
                kept=kept.code,
                dropped=[p.code for p in others],
                name=kept.name,
                message=(
                    f"В КТРУ {len(group)} позиции «{kept.name}» с одинаковыми характеристиками "
                    f"({roles}): по описанию их не различить. Взята {kept.code} — более свежая: "
                    f"{_why(kept, others, meta)}."
                ),
            )
        )
    return [p for p in positions if p.code in keep], merges
