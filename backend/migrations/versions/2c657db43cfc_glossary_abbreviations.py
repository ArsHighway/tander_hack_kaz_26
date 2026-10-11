"""glossary abbreviations

Revision ID: 2c657db43cfc
Revises: 98d7eb7484ff
Create Date: 2026-09-30 17:36:21.423166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c657db43cfc'
down_revision: Union[str, Sequence[str], None] = '98d7eb7484ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "glossary_abbreviation",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("abbr", sa.Text, nullable=False),
        sa.Column("full", sa.Text, nullable=False, server_default=""),
        sa.Column("kind", sa.Text, nullable=False, server_default=""),
        # seed — наш словарь, mined — найдено в данных организатора, manual — поправлено руками
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("case_insensitive", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("occurrences", sa.Integer, nullable=False, server_default="0"),
        sa.Column("examples", sa.Text, nullable=False, server_default=""),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.CheckConstraint("source IN ('seed', 'mined', 'manual')", name="glossary_source_check"),
        sa.UniqueConstraint("abbr", "full", name="glossary_abbr_full_uq"),
    )
    op.create_index("glossary_abbr_idx", "glossary_abbreviation", ["abbr"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("glossary_abbr_idx", table_name="glossary_abbreviation")
    op.drop_table("glossary_abbreviation")
