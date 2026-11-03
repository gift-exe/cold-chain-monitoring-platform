"""Notification: SMS and dashboard notifications, one-time response codes, incoming SMS (iteration I5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SCHEMA = """
-- Short numbers in SMS: incidents already have incident_no; consignments get one too (REC 312 ...)
ALTER TABLE consignment ADD COLUMN consignment_no bigserial UNIQUE;

-- Response codes are kept per recipient in response_code, not one per incident
ALTER TABLE incident
    DROP COLUMN response_code_hash,
    ADD COLUMN notification_failed boolean NOT NULL DEFAULT false;   -- Table 3.20, shown on the dashboard

-- A lost phone: the administrator deactivates the number and every command from it is rejected
ALTER TABLE app_user ADD COLUMN phone_deactivated_at timestamptz;

ALTER TABLE incident_action
    DROP CONSTRAINT incident_action_action_check,
    ADD CONSTRAINT incident_action_action_check CHECK (action IN (
        'created', 'notified', 'delivery_report', 'acknowledged', 'escalated', 'action_recorded',
        'resolved', 'reopened', 'closed', 'reminder', 'overdue', 'updated', 'condition_cleared', 'linked',
        'notification_failed'));

-- One message to one person on one channel. A notification request (from the outbox) becomes one row per
-- recipient and channel; (request_id, recipient_id, channel) makes that idempotent, so a request
-- delivered twice by Kafka is sent once. SMS bodies are stored with the response code masked.
CREATE TABLE notification (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id         uuid NOT NULL,
    kind               text NOT NULL CHECK (kind IN ('alert', 'cc', 'escalation', 'overdue', 'reminder',
                                                     'reopened', 'acknowledged', 'digest', 'handoff',
                                                     'undelivered', 'admin', 'reply')),
    channel            text NOT NULL CHECK (channel IN ('sms', 'dashboard')),
    recipient_id       uuid NOT NULL REFERENCES app_user (id),
    phone_number       text,
    incident_id        uuid REFERENCES incident (id),
    consignment_id     uuid REFERENCES consignment (id),
    handoff_id         uuid REFERENCES custody_handoff (id),
    tier               text,
    reason             text,                  -- why it was sent, if not the kind alone (e.g. undelivered)
    body               text NOT NULL,
    -- sms: held (waiting for the digest) -> digested, or queued -> sent -> delivered | retrying -> ...
    --      | undelivered | rejected; dashboard: posted
    status             text NOT NULL CHECK (status IN ('held', 'digested', 'queued', 'sent', 'retrying',
                                                       'delivered', 'undelivered', 'rejected', 'posted')),
    attempts           integer NOT NULL DEFAULT 0,
    gateway_message_id text UNIQUE,
    gateway_status     text,                  -- the gateway's latest status, e.g. Buffered
    failure_reason     text,
    digest_id          uuid REFERENCES notification (id),
    due_at             timestamptz NOT NULL DEFAULT now(),   -- when to send (retry, digest) or post
    created_at         timestamptz NOT NULL DEFAULT now(),
    first_sent_at      timestamptz,           -- dispatch time for latency (E4)
    sent_at            timestamptz,           -- latest attempt
    delivered_at       timestamptz,
    read_at            timestamptz,           -- dashboard notifications
    UNIQUE (request_id, recipient_id, channel)
);
CREATE INDEX notification_due ON notification (due_at) WHERE status IN ('queued', 'retrying', 'held');
CREATE INDEX notification_awaiting ON notification (first_sent_at) WHERE status = 'sent';
CREATE INDEX notification_recipient ON notification (recipient_id, created_at) WHERE channel = 'dashboard';
CREATE INDEX notification_incident ON notification (incident_id);

-- One-time response codes (Table 3.19): one per recipient per incident or handoff, sent in the SMS.
-- Only an HMAC of the code is stored (keyed with a secret outside the database, so the short code cannot
-- be recovered by trying every value against the stored hash).
CREATE TABLE response_code (
    id              bigserial PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES app_user (id),
    incident_id     uuid REFERENCES incident (id),
    handoff_id      uuid REFERENCES custody_handoff (id),
    code_hash       text NOT NULL,
    issued_at       timestamptz NOT NULL,
    expires_at      timestamptz NOT NULL,
    used_at         timestamptz,
    voided_at       timestamptz,
    failed_attempts integer NOT NULL DEFAULT 0,
    CHECK ((incident_id IS NULL) <> (handoff_id IS NULL))
);
CREATE INDEX response_code_valid ON response_code (user_id, incident_id, handoff_id)
    WHERE used_at IS NULL AND voided_at IS NULL;

-- Every incoming SMS and what was done with it (E8 evidence). The gateway's message id makes duplicate
-- deliveries harmless; any response code in the text is masked before it is stored.
CREATE TABLE sms_inbound (
    id             bigserial PRIMARY KEY,
    gateway_id     text NOT NULL UNIQUE,
    from_number    text NOT NULL,
    to_number      text,
    text           text NOT NULL,
    link_id        text,
    received_at    timestamptz NOT NULL DEFAULT now(),
    user_id        uuid REFERENCES app_user (id),
    command        text,
    outcome        text NOT NULL CHECK (outcome IN ('accepted', 'no_effect', 'rejected', 'rate_limited')),
    reason         text,
    incident_id    uuid REFERENCES incident (id),
    consignment_id uuid REFERENCES consignment (id)
);
CREATE INDEX sms_inbound_rate ON sms_inbound (from_number, received_at);
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON notification, response_code, sms_inbound TO coldchain_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO coldchain_app;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE sms_inbound, response_code, notification;
        ALTER TABLE incident_action DROP CONSTRAINT incident_action_action_check,
            ADD CONSTRAINT incident_action_action_check CHECK (action IN (
                'created', 'notified', 'delivery_report', 'acknowledged', 'escalated', 'action_recorded',
                'resolved', 'reopened', 'closed', 'reminder', 'overdue', 'updated', 'condition_cleared', 'linked'));
        ALTER TABLE app_user DROP COLUMN phone_deactivated_at;
        ALTER TABLE incident DROP COLUMN notification_failed, ADD COLUMN response_code_hash text;
        ALTER TABLE consignment DROP COLUMN consignment_no;
    """)
