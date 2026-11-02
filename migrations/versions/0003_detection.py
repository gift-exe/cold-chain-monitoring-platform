"""Detection support: freeze limit per product, local alarm flag, event severity, device status.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        -- Freeze threshold per product (Table 3.17, default 0 °C), separate from the storage range
        ALTER TABLE product ADD COLUMN freeze_limit numeric(4, 1) NOT NULL DEFAULT 0.0;
        ALTER TABLE product ADD CONSTRAINT product_freeze_below_upper CHECK (freeze_limit < upper_limit);

        -- Device reports that its local alarm sounded
        ALTER TABLE reading ADD COLUMN alarm boolean;

        ALTER TABLE integrity_event
            ADD COLUMN severity text NOT NULL DEFAULT 'warning' CHECK (severity IN ('critical', 'warning')),
            ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
        CREATE INDEX integrity_event_device_type_start ON integrity_event (device_id, type, event_start);

        -- Latest contact per device, maintained by the storage writer; used to detect device silence
        CREATE TABLE device_status (
            device_id        uuid PRIMARY KEY REFERENCES device (id) ON DELETE CASCADE,
            last_event_time  timestamptz NOT NULL,
            last_received_at timestamptz NOT NULL
        );
        GRANT SELECT, INSERT, UPDATE, DELETE ON device_status TO coldchain_app;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE device_status;
        DROP INDEX integrity_event_device_type_start;
        ALTER TABLE integrity_event DROP COLUMN severity, DROP COLUMN updated_at;
        ALTER TABLE reading DROP COLUMN alarm;
        ALTER TABLE product DROP CONSTRAINT product_freeze_below_upper, DROP COLUMN freeze_limit;
    """)
