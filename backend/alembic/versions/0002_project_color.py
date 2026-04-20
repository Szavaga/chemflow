"""Add color column to projects.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("color", sa.String(7), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "color")
