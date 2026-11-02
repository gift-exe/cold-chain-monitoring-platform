"""Rejected device messages, with the reason for rejection (FR1.3).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE rejected_message (
            id          bigserial PRIMARY KEY,
            received_at timestamptz NOT NULL,
            topic       text NOT NULL,
            serial      text,
            reason      text NOT NULL,
            payload     text NOT NULL
        );
        CREATE INDEX rejected_message_received_at ON rejected_message (received_at);
        GRANT SELECT, INSERT ON rejected_message TO coldchain_app;
        GRANT USAGE, SELECT ON SEQUENCE rejected_message_id_seq TO coldchain_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE rejected_message;")
