"""Facility ward (E9, problem P07): the dashboard shows where a facility is as ward, LGA and state. Wards come
from GRID3 (Section 3.3.5); a facility may have none.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE facility ADD COLUMN ward text")


def downgrade() -> None:
    op.execute("ALTER TABLE facility DROP COLUMN ward")
