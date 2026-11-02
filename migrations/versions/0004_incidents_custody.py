"""Incidents, escalation, custody handoffs and the hash-chained audit trail (iteration I4).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CHAINED = {"incident_action": ("id", "incident_action_id_seq"), "custody_event": ("seq", "custody_event_seq_seq")}

SCHEMA = """
-- Escalation policy (Table 3.15) ---------------------------------------------------------------
-- kind: 'acknowledge' stages are measured from incident creation (or from re-notification after a
-- recurrence); 'resolve' stages from acknowledgement (or from a reviewer's reopening); 'reminder' is the
-- repeat interval of reminders once an incident is overdue at the top tier.
-- tier 'next' means the tier above the highest tier already notified.
ALTER TABLE escalation_policy
    ADD COLUMN kind text NOT NULL DEFAULT 'acknowledge' CHECK (kind IN ('acknowledge', 'resolve', 'reminder')),
    DROP CONSTRAINT escalation_policy_severity_mode_stage_key,
    DROP CONSTRAINT escalation_policy_tier_check,
    ADD CONSTRAINT escalation_policy_tier_check CHECK (tier IN ('owner', 'lga', 'state', 'national', 'next')),
    ADD CONSTRAINT escalation_policy_stage_key UNIQUE (severity, mode, kind, stage);

INSERT INTO escalation_policy (severity, mode, kind, stage, tier, delay_min) VALUES
    ('critical', 'operational',   'resolve',  1, 'next',     120),
    ('critical', 'operational',   'resolve',  2, 'next',     240),
    ('critical', 'operational',   'reminder', 1, 'national', 1440),
    ('critical', 'retrospective', 'resolve',  1, 'state',    10080);

-- Incidents ------------------------------------------------------------------------------------
ALTER TABLE incident
    ADD COLUMN type             text NOT NULL,
    ADD COLUMN device_id        uuid REFERENCES device (id),
    ADD COLUMN consignment_id   uuid REFERENCES consignment (id),
    ADD COLUMN jurisdiction_id  text NOT NULL REFERENCES jurisdiction (id),  -- routes escalations
    ADD COLUMN cc_ids           uuid[] NOT NULL DEFAULT '{}',   -- copied on notifications (CR2, CR4)
    ADD COLUMN ownership        text NOT NULL,                  -- the rule that chose the owner
    ADD COLUMN received_at      timestamptz,                    -- receipt of the triggering reading
    ADD COLUMN detected_at      timestamptz NOT NULL,
    ADD COLUMN acknowledged_by  uuid REFERENCES app_user (id),
    ADD COLUMN resolved_by      uuid REFERENCES app_user (id),
    ADD COLUMN closed_by        uuid REFERENCES app_user (id),
    ADD COLUMN reopened_at      timestamptz,
    ADD COLUMN reopen_count     integer NOT NULL DEFAULT 0,
    -- Escalation clock: the policy stages already applied and the times they are measured from
    ADD COLUMN ack_due_from     timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN ack_stage        integer NOT NULL DEFAULT -1,    -- -1: owner not yet notified
    ADD COLUMN resolve_due_from timestamptz,
    ADD COLUMN resolve_stage    integer NOT NULL DEFAULT 0,
    ADD COLUMN top_tier_at      timestamptz,
    ADD COLUMN last_reminder_at timestamptz,
    ADD CHECK (device_id IS NOT NULL OR consignment_id IS NOT NULL);
CREATE INDEX incident_device_type ON incident (device_id, type, resolved_at);
CREATE INDEX incident_open ON incident (state) WHERE state <> 'closed';

-- An integrity event belongs to at most one incident; an incident may gather several events
-- (a recurrence that reopened it, or a local alarm recorded on it)
ALTER TABLE integrity_event ADD COLUMN incident_id uuid REFERENCES incident (id);

ALTER TABLE incident_action
    DROP CONSTRAINT incident_action_action_check,
    ADD CONSTRAINT incident_action_action_check CHECK (action IN (
        'created', 'notified', 'delivery_report', 'acknowledged', 'escalated', 'action_recorded',
        'resolved', 'reopened', 'closed', 'reminder', 'overdue', 'updated', 'condition_cleared', 'linked'));

-- Custody ----------------------------------------------------------------------------------------
-- A handoff from release to confirmation, rejection or cancellation (CR1-CR5). Custody intervals
-- record who held the consignment; the handoff records what is in progress.
CREATE TABLE custody_handoff (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    consignment_id uuid NOT NULL REFERENCES consignment (id),
    from_user      uuid NOT NULL REFERENCES app_user (id),
    to_user        uuid NOT NULL REFERENCES app_user (id),
    released_at    timestamptz NOT NULL,
    window_ends_at timestamptz NOT NULL,                    -- expected transit + margin (CR4)
    state          text NOT NULL DEFAULT 'pending'
                   CHECK (state IN ('pending', 'confirmed', 'rejected', 'cancelled')),
    ended_at       timestamptz,                             -- when confirmed, rejected or cancelled
    reason         text,
    code_hash      text,                                    -- confirmation code (sent by SMS, I5)
    gap_event_id   uuid REFERENCES integrity_event (id),
    CHECK (window_ends_at > released_at),
    CHECK ((state = 'pending') = (ended_at IS NULL)),
    CHECK (from_user <> to_user)
);
-- At most one open handoff per consignment: pending, or rejected and not yet withdrawn by the releaser
-- (withdrawing a rejected handoff sets it to 'cancelled'; ended_at keeps the time of rejection)
CREATE UNIQUE INDEX custody_handoff_open ON custody_handoff (consignment_id) WHERE state IN ('pending', 'rejected');

ALTER TABLE custody_event
    DROP CONSTRAINT custody_event_type_check,
    ADD CONSTRAINT custody_event_type_check CHECK (type IN (
        'create', 'release', 'confirm', 'reject', 'cancel', 'complete', 'correction')),
    ADD COLUMN handoff_id uuid REFERENCES custody_handoff (id),
    ADD COLUMN actor_id   uuid REFERENCES app_user (id),
    ADD COLUMN seq        bigserial UNIQUE,
    ADD COLUMN prev_hash  bytea,
    ADD COLUMN hash       bytea;

ALTER TABLE consignment ADD COLUMN completed_at timestamptz;

-- Mobile loggers are silent if they have not uploaded within their synchronisation window (Table 3.17)
ALTER TABLE device ADD COLUMN sync_window_min integer NOT NULL DEFAULT 1440 CHECK (sync_window_min > 0);

-- Transactional outbox: messages for the event log (notifications, custody-gap events) are written in
-- the same transaction as the change that causes them, and relayed to Kafka afterwards, so a crash
-- between the database commit and the publish cannot lose them.
CREATE TABLE outbox (
    id         bigserial PRIMARY KEY,
    topic      text NOT NULL,
    key        text,
    payload    jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at    timestamptz
);
CREATE INDEX outbox_unsent ON outbox (id) WHERE sent_at IS NULL;

-- Hash chain (Section 3.4.6) ---------------------------------------------------------------------
-- Each record's hash is SHA-256 over the previous record's hash and the record's own contents (as
-- JSON, without nulls, so adding a nullable column later does not change old hashes). The chain
-- position is assigned inside a lock, so positions follow commit order and concurrent inserts cannot
-- fork the chain. The timezone is fixed so timestamps always serialise the same way.
CREATE FUNCTION chain_hash() RETURNS trigger LANGUAGE plpgsql SET timezone = 'UTC' AS $$
DECLARE
    pos_col text := TG_ARGV[0];
    prev    bytea;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('chain:' || TG_TABLE_NAME));
    EXECUTE format('SELECT hash FROM %I ORDER BY %I DESC LIMIT 1', TG_TABLE_NAME, pos_col) INTO prev;
    NEW := jsonb_populate_record(NEW, jsonb_build_object(pos_col, nextval(TG_ARGV[1])));
    NEW.prev_hash := coalesce(prev, ''::bytea);
    NEW.hash := sha256(NEW.prev_hash
                       || convert_to(jsonb_strip_nulls(to_jsonb(NEW) - 'prev_hash' - 'hash')::text, 'UTF8'));
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    for table, (pos, seq) in CHAINED.items():
        op.execute(f"""
            CREATE TRIGGER {table}_chain BEFORE INSERT ON {table}
                FOR EACH ROW EXECUTE FUNCTION chain_hash('{pos}', '{seq}');
        """)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON custody_handoff, outbox TO coldchain_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO coldchain_app;
    """)


def downgrade() -> None:
    for table in CHAINED:
        op.execute(f"DROP TRIGGER {table}_chain ON {table};")
    op.execute("""
        DROP FUNCTION chain_hash();
        ALTER TABLE device DROP COLUMN sync_window_min;
        ALTER TABLE consignment DROP COLUMN completed_at;
        ALTER TABLE custody_event DROP COLUMN handoff_id, DROP COLUMN actor_id, DROP COLUMN seq,
            DROP COLUMN prev_hash, DROP COLUMN hash,
            DROP CONSTRAINT custody_event_type_check,
            ADD CONSTRAINT custody_event_type_check CHECK (type IN ('release', 'confirm', 'reject', 'correction'));
        DROP TABLE custody_handoff, outbox;
        ALTER TABLE incident_action DROP CONSTRAINT incident_action_action_check,
            ADD CONSTRAINT incident_action_action_check CHECK (action IN ('created', 'notified',
                'delivery_report', 'acknowledged', 'escalated', 'action_recorded', 'resolved', 'reopened',
                'closed', 'reminder'));
        ALTER TABLE integrity_event DROP COLUMN incident_id;
        DROP INDEX incident_device_type, incident_open;
        ALTER TABLE incident DROP COLUMN type, DROP COLUMN device_id, DROP COLUMN consignment_id,
            DROP COLUMN jurisdiction_id, DROP COLUMN cc_ids, DROP COLUMN ownership, DROP COLUMN received_at,
            DROP COLUMN detected_at, DROP COLUMN acknowledged_by, DROP COLUMN resolved_by,
            DROP COLUMN closed_by, DROP COLUMN reopened_at, DROP COLUMN reopen_count,
            DROP COLUMN ack_due_from, DROP COLUMN ack_stage, DROP COLUMN resolve_due_from,
            DROP COLUMN resolve_stage, DROP COLUMN top_tier_at, DROP COLUMN last_reminder_at;
        DELETE FROM escalation_policy WHERE kind <> 'acknowledge';
        ALTER TABLE escalation_policy DROP CONSTRAINT escalation_policy_stage_key,
            DROP CONSTRAINT escalation_policy_tier_check,
            ADD CONSTRAINT escalation_policy_tier_check CHECK (tier IN ('owner', 'lga', 'state', 'national')),
            DROP COLUMN kind, ADD CONSTRAINT escalation_policy_severity_mode_stage_key UNIQUE (severity, mode, stage);
    """)
