"""Dashboard: an audit trail of administrative changes, and change notices for live updates (iteration I6).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

SCHEMA = """
-- FR11.2: every administrative change (product limits, phone numbers, ...) is recorded, hash-chained and
-- append-only like the incident audit trail
CREATE TABLE admin_action (
    id          bigserial PRIMARY KEY,
    actor_id    uuid NOT NULL REFERENCES app_user (id),
    action      text NOT NULL,
    target_type text NOT NULL,
    target_id   text NOT NULL,
    before      jsonb,
    after       jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    prev_hash   bytea,
    hash        bytea
);
CREATE INDEX admin_action_target ON admin_action (target_type, target_id);
CREATE TRIGGER admin_action_chain BEFORE INSERT ON admin_action
    FOR EACH ROW EXECUTE FUNCTION chain_hash('id', 'admin_action_id_seq');
CREATE TRIGGER admin_action_no_update_delete BEFORE UPDATE OR DELETE ON admin_action
    FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER admin_action_no_truncate BEFORE TRUNCATE ON admin_action
    FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
GRANT SELECT, INSERT ON admin_action TO coldchain_app;
GRANT USAGE, SELECT ON SEQUENCE admin_action_id_seq TO coldchain_app;

-- Live updates: the API listens on this channel and tells connected dashboards what kind of data
-- changed. One notice per statement, not per row, so a batch of readings is one notice.
CREATE FUNCTION notify_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('coldchain_changes', TG_TABLE_NAME);
    RETURN NULL;
END;
$$;
"""

WATCHED = ("incident", "notification", "consignment", "custody_handoff", "device_status")


def upgrade() -> None:
    op.execute(SCHEMA)
    for table in WATCHED:
        op.execute(f"""CREATE TRIGGER {table}_changed AFTER INSERT OR UPDATE ON {table}
                       FOR EACH STATEMENT EXECUTE FUNCTION notify_change()""")


def downgrade() -> None:
    for table in WATCHED:
        op.execute(f"DROP TRIGGER {table}_changed ON {table}")
    op.execute("DROP FUNCTION notify_change(); DROP TABLE admin_action;")
