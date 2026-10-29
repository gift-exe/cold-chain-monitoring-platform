"""Initial schema: the entities in the data dictionary (Chapter 3, Table 3.18).

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tables that may only be appended to (FR7.2, CR9)
APPEND_ONLY = ("incident_action", "custody_event")

ROLES = ("national_officer", "state_officer", "lga_officer", "facility_oic", "outreach_lead", "driver", "admin")

SCHEMA = f"""
-- Reference data ------------------------------------------------------------

CREATE TABLE jurisdiction (
    id          text PRIMARY KEY,                          -- e.g. NG, NG-KN, NG-KN-GWL
    level       text NOT NULL CHECK (level IN ('nation', 'state', 'lga')),
    name        text NOT NULL,
    parent_id   text REFERENCES jurisdiction (id),
    boundary    geography(MultiPolygon, 4326),
    CHECK ((level = 'nation') = (parent_id IS NULL))       -- only the nation has no parent
);

CREATE TABLE facility (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL,
    tier                text NOT NULL CHECK (tier IN ('national_store', 'zonal_store', 'state_store',
                                                      'lga_store', 'health_facility', 'outreach_base')),
    jurisdiction_id     text NOT NULL REFERENCES jurisdiction (id),
    parent_facility_id  uuid REFERENCES facility (id),
    location            geography(Point, 4326) NOT NULL,
    power_source        text NOT NULL CHECK (power_source IN ('grid', 'solar', 'generator',
                                                              'grid_generator', 'none'))
);

CREATE TABLE device (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    serial_no            text NOT NULL UNIQUE,
    facility_id          uuid REFERENCES facility (id),
    type                 text NOT NULL CHECK (type IN ('fixed', 'mobile')),
    equipment_type       text NOT NULL,
    has_door_sensor      boolean NOT NULL DEFAULT false,
    has_power_signal     boolean NOT NULL DEFAULT false,
    reporting_interval_s integer NOT NULL DEFAULT 60 CHECK (reporting_interval_s > 0),
    status               text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'retired')),
    CHECK (type = 'mobile' OR facility_id IS NOT NULL)     -- fixed devices belong to a facility
);

CREATE TABLE product (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL UNIQUE,
    lower_limit          numeric(4, 1) NOT NULL DEFAULT 2.0,
    upper_limit          numeric(4, 1) NOT NULL DEFAULT 8.0,
    freeze_sensitive     boolean NOT NULL DEFAULT true,
    heat_tolerance_min   integer NOT NULL DEFAULT 30 CHECK (heat_tolerance_min >= 0),
    freeze_tolerance_min integer NOT NULL DEFAULT 5 CHECK (freeze_tolerance_min >= 0),
    CHECK (lower_limit < upper_limit)
);

-- Users are authenticated by Keycloak; id is the Keycloak subject identifier.
CREATE TABLE app_user (
    id              uuid PRIMARY KEY,
    username        text NOT NULL UNIQUE,
    full_name       text NOT NULL,
    role            text NOT NULL CHECK (role IN ({", ".join(f"'{r}'" for r in ROLES)})),
    jurisdiction_id text NOT NULL REFERENCES jurisdiction (id),
    facility_id     uuid REFERENCES facility (id),
    phone_number    text CHECK (phone_number ~ '^\\+[1-9][0-9]{{7,14}}$'),   -- E.164
    active          boolean NOT NULL DEFAULT true
);
-- A phone number identifies one active user, so SMS replies are attributable (NFR7)
CREATE UNIQUE INDEX app_user_active_phone ON app_user (phone_number) WHERE active;

-- Telemetry -----------------------------------------------------------------

-- Hypertable partitioned by event_time. TimescaleDB requires the partitioning column in every
-- unique key, so the key is (device_id, event_time, seq_no): a retransmitted reading carries the
-- same seq_no and event_time and is discarded by ON CONFLICT DO NOTHING (FR1.4).
CREATE TABLE reading (
    device_id    uuid NOT NULL REFERENCES device (id),
    seq_no       bigint NOT NULL CHECK (seq_no >= 0),
    event_time   timestamptz NOT NULL,
    received_at  timestamptz NOT NULL,
    processed_at timestamptz,
    temperature  numeric(5, 2),
    power_ok     boolean,
    door_open    boolean,
    location     geography(Point, 4326),
    battery      smallint CHECK (battery BETWEEN 0 AND 100),
    late_flag    boolean NOT NULL DEFAULT false,           -- arrived beyond the 72 h horizon (FR2.3)
    PRIMARY KEY (device_id, event_time, seq_no),
    CHECK (event_time <= received_at + interval '5 minutes')   -- FR2.4
);
SELECT create_hypertable('reading', 'event_time', chunk_time_interval => interval '1 day');

-- Consignments and custody ---------------------------------------------------

CREATE TABLE consignment (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id           uuid NOT NULL REFERENCES product (id),
    quantity             integer NOT NULL CHECK (quantity > 0),
    origin_facility_id   uuid NOT NULL REFERENCES facility (id),
    destination          text NOT NULL,
    expected_transit_min integer NOT NULL CHECK (expected_transit_min > 0),
    created_at           timestamptz NOT NULL DEFAULT now()
);

-- Which consignment a logger's readings belong to, and when (CR8)
CREATE TABLE device_assignment (
    device_id      uuid NOT NULL REFERENCES device (id),
    consignment_id uuid NOT NULL REFERENCES consignment (id),
    valid_from     timestamptz NOT NULL,
    valid_to       timestamptz,                            -- null while current
    PRIMARY KEY (device_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (device_id WITH =, tstzrange(valid_from, valid_to) WITH &&)
);

-- Who held a consignment, and when (CR1-CR3, CR6). At most one holder at any instant.
CREATE TABLE custody_interval (
    consignment_id uuid NOT NULL REFERENCES consignment (id),
    holder_id      uuid NOT NULL REFERENCES app_user (id),
    valid_from     timestamptz NOT NULL,
    valid_to       timestamptz,                            -- null while current
    PRIMARY KEY (consignment_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (consignment_id WITH =, tstzrange(valid_from, valid_to) WITH &&)
);

CREATE TABLE custody_event (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    consignment_id uuid NOT NULL REFERENCES consignment (id),
    type           text NOT NULL CHECK (type IN ('release', 'confirm', 'reject', 'correction')),
    from_user      uuid REFERENCES app_user (id),
    to_user        uuid REFERENCES app_user (id),
    occurred_at    timestamptz NOT NULL DEFAULT now(),
    code_hash      text,
    details        jsonb NOT NULL DEFAULT '{{}}'
);

-- Detection and incidents ----------------------------------------------------

CREATE TABLE integrity_event (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id      uuid REFERENCES device (id),
    consignment_id uuid REFERENCES consignment (id),       -- for custody gaps
    type           text NOT NULL CHECK (type IN ('heat_excursion', 'freeze', 'power_loss', 'door_open',
                                                 'device_silence', 'custody_gap', 'local_alarm', 'anomaly')),
    event_start    timestamptz NOT NULL,
    event_end      timestamptz,                            -- null while continuing
    peak_value     numeric(5, 2),
    detected_at    timestamptz NOT NULL DEFAULT now(),
    borderline     boolean NOT NULL DEFAULT false,
    details        jsonb NOT NULL DEFAULT '{{}}',
    CHECK (device_id IS NOT NULL OR consignment_id IS NOT NULL),
    CHECK (event_end IS NULL OR event_end >= event_start)
);

CREATE TABLE escalation_policy (
    id        serial PRIMARY KEY,
    severity  text NOT NULL CHECK (severity IN ('critical', 'warning')),
    mode      text NOT NULL CHECK (mode IN ('operational', 'retrospective')),
    stage     integer NOT NULL CHECK (stage >= 0),
    tier      text NOT NULL CHECK (tier IN ('owner', 'lga', 'state', 'national')),
    delay_min integer NOT NULL CHECK (delay_min >= 0),     -- cumulative from incident creation
    UNIQUE (severity, mode, stage)
);

CREATE TABLE incident (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_no          bigserial UNIQUE,                 -- short number used in SMS (e.g. 4821)
    incident_key         text NOT NULL UNIQUE,             -- device/consignment + type + event start (idempotent replay)
    integrity_event_id   uuid NOT NULL UNIQUE REFERENCES integrity_event (id),
    severity             text NOT NULL CHECK (severity IN ('critical', 'warning')),
    mode                 text NOT NULL CHECK (mode IN ('operational', 'retrospective')),
    state                text NOT NULL DEFAULT 'open'
                         CHECK (state IN ('open', 'notified', 'acknowledged', 'escalated', 'resolved', 'closed')),
    owner_id             uuid NOT NULL REFERENCES app_user (id),
    current_tier         text NOT NULL DEFAULT 'owner' CHECK (current_tier IN ('owner', 'lga', 'state', 'national')),
    previous_incident_id uuid REFERENCES incident (id),    -- linked recurrence (FR5.5)
    condition_cleared    boolean NOT NULL DEFAULT false,
    overdue              boolean NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL DEFAULT now(),
    notified_at          timestamptz,
    acknowledged_at      timestamptz,
    resolved_at          timestamptz,
    closed_at            timestamptz,
    response_code_hash   text
);

-- The audit trail (FR7). Append-only; hash chain filled in from iteration I4.
CREATE TABLE incident_action (
    id          bigserial PRIMARY KEY,
    incident_id uuid NOT NULL REFERENCES incident (id),
    action      text NOT NULL CHECK (action IN ('created', 'notified', 'delivery_report', 'acknowledged',
                                                'escalated', 'action_recorded', 'resolved', 'reopened',
                                                'closed', 'reminder')),
    actor_id    uuid REFERENCES app_user (id),             -- null when the system acts
    channel     text NOT NULL CHECK (channel IN ('sms', 'dashboard', 'system')),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    details     jsonb NOT NULL DEFAULT '{{}}',
    prev_hash   bytea,
    hash        bytea
);

-- Append-only enforcement ----------------------------------------------------

CREATE FUNCTION reject_modification() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;
"""

DEFAULT_POLICY = """
INSERT INTO escalation_policy (severity, mode, stage, tier, delay_min) VALUES
    ('critical', 'operational',   0, 'owner',    0),
    ('critical', 'operational',   1, 'lga',      15),
    ('critical', 'operational',   2, 'state',    60),
    ('critical', 'operational',   3, 'national', 240),
    ('critical', 'retrospective', 0, 'owner',    0),
    ('critical', 'retrospective', 1, 'lga',      1440),
    ('critical', 'retrospective', 2, 'state',    4320),
    ('warning',  'operational',   0, 'owner',    0),
    ('warning',  'operational',   1, 'lga',      1440),
    ('warning',  'retrospective', 0, 'owner',    0),
    ('warning',  'retrospective', 1, 'lga',      1440);
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    for table in APPEND_ONLY:
        op.execute(f"""
            CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_modification();
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
        """)
    op.execute(DEFAULT_POLICY)

    # Privileges for the services' role: full use of operational tables, insert-only on the audit trail
    op.execute("""
        GRANT USAGE ON SCHEMA public TO coldchain_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO coldchain_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO coldchain_app;
    """)
    for table in APPEND_ONLY:
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM coldchain_app;")


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS incident_action, incident, escalation_policy, integrity_event, custody_event,
            custody_interval, device_assignment, consignment, reading, app_user, product, device, facility,
            jurisdiction CASCADE;
        DROP FUNCTION IF EXISTS reject_modification();
    """)
