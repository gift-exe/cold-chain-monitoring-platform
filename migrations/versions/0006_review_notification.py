"""Review notification: a resolved incident tells its reviewers on the dashboard (found in sandbox testing).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

KINDS = ("'alert', 'cc', 'escalation', 'overdue', 'reminder', 'reopened', 'acknowledged', 'digest', 'handoff', "
         "'undelivered', 'admin', 'reply'")


def _kinds(extra: str) -> str:
    return f"""ALTER TABLE notification DROP CONSTRAINT notification_kind_check,
                   ADD CONSTRAINT notification_kind_check CHECK (kind IN ({KINDS}{extra}))"""


def upgrade() -> None:
    op.execute(_kinds(", 'review'"))


def downgrade() -> None:
    op.execute("DELETE FROM notification WHERE kind = 'review'")
    op.execute(_kinds(""))
