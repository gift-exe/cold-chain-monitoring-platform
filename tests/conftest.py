import json
from pathlib import Path

import psycopg
import pytest

from coldchain.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


def _pg_url(role: str) -> str:
    return get_settings().db_url(role).replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture
def owner_conn():
    """Connection as the schema owner. Everything done in a test is rolled back."""
    with psycopg.connect(_pg_url("owner")) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def app_conn():
    """Connection as the services' role (coldchain_app). Rolled back after the test."""
    with psycopg.connect(_pg_url("app")) as conn:
        yield conn
        conn.rollback()


@pytest.fixture(scope="session")
def realm_users() -> list[dict]:
    realm = json.loads((ROOT / "infra/keycloak/coldchain-realm.json").read_text())
    return realm["users"]


# Seed data (seeds/dev_reference.sql, seeds/dev_users.sql) ----------------------------------------------

USERS = {
    "national": "0b1e5a10-0000-4000-8000-000000000001", "state": "0b1e5a10-0000-4000-8000-000000000002",
    "lga": "0b1e5a10-0000-4000-8000-000000000003", "oic": "0b1e5a10-0000-4000-8000-000000000004",
    "outreach": "0b1e5a10-0000-4000-8000-000000000005", "driver": "0b1e5a10-0000-4000-8000-000000000006",
    "admin": "0b1e5a10-0000-4000-8000-000000000007",
}
HEALTH_FACILITY = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d01"   # Gwale LGA; officer-in-charge: USERS["oic"]
PRODUCT = "5c1d0a00-0000-4000-8000-000000000001"


def close_test_incidents(device_ids=(), consignment_ids=()) -> None:
    """Test teardown. The audit trail is append-only, so test data that has incidents cannot be deleted.
    Instead, every integrity event of the test's devices and consignments is given its incident (as the
    incident service would; doing it twice is harmless), the incidents are resolved and closed so their
    workflows finish, and the devices are retired so silence detection ignores them."""
    from coldchain.incidents import store
    devices, consignments = list(device_ids), list(consignment_ids)
    with psycopg.connect(_pg_url("app"), autocommit=True) as conn:
        unhandled = conn.execute("""
            SELECT id::text FROM integrity_event WHERE incident_id IS NULL AND type <> 'local_alarm'
              AND (device_id = ANY(%s::uuid[]) OR consignment_id = ANY(%s::uuid[]))""",
            (devices, consignments)).fetchall()
        for (event_id,) in unhandled:
            with conn.transaction():
                store.on_event(conn, event_id)
        rows = conn.execute("""
            SELECT id::text, owner_id::text, state FROM incident WHERE state <> 'closed'
              AND (device_id = ANY(%s::uuid[]) OR consignment_id = ANY(%s::uuid[]))""",
            (devices, consignments)).fetchall()
        for incident_id, owner, state in rows:
            with conn.transaction():
                if state != "resolved":
                    store.resolve(conn, incident_id, owner, "test finished")
                store.close(conn, incident_id, USERS["national"], "test finished")
        conn.execute("UPDATE device SET status = 'retired' WHERE id = ANY(%s::uuid[])", (devices,))


def retire_devices(device_ids: list[str]) -> None:
    close_test_incidents(device_ids)
