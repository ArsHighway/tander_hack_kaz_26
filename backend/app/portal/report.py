"""Отчёт по живым карточкам портала: аудит модерации и заполнение — для страницы «Метрики»."""

from datetime import UTC, datetime

from pydantic import BaseModel

from app.evaluation.metrics import MetricRow
from app.portal.audit import PortalAudit, audit
from app.portal.cards import PortalCard
from app.portal.fill import compare_card, fill_rows
from app.portal.values import ValueIndex


class PortalReport(BaseModel):
    created_at: datetime
    cards: int
    categories: int
    source: str
    audit: PortalAudit
    fill: list[MetricRow]


def build_report(cards: list[PortalCard], source: str = "") -> PortalReport:
    index = ValueIndex(cards)
    outcomes = {c.id: compare_card(c, index) for c in cards}
    return PortalReport(
        created_at=datetime.now(UTC),
        cards=len(cards),
        categories=len({c.category_path for c in cards}),
        source=source,
        audit=audit(cards, index, outcomes),
        fill=fill_rows([o for os in outcomes.values() for o in os]),
    )
