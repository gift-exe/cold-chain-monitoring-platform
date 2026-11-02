"""I1 acceptance: the stack starts from a single command and every service is reachable."""
import socket

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from coldchain.config import get_settings
from tests.conftest import ROOT

pytestmark = pytest.mark.integration
S = get_settings()


def test_database_extensions_installed(owner_conn):
    names = {r[0] for r in owner_conn.execute("SELECT extname FROM pg_extension")}
    assert {"timescaledb", "postgis", "btree_gist"} <= names


def test_migrations_at_head(owner_conn):
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    assert owner_conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head


def test_reading_is_a_hypertable(owner_conn):
    row = owner_conn.execute(
        "SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'reading'").fetchone()
    assert row is not None


def test_keycloak_realm_published():
    r = httpx.get(f"{S.keycloak_url}/realms/{S.keycloak_realm}/.well-known/openid-configuration", timeout=10)
    assert r.status_code == 200
    assert r.json()["issuer"].endswith("/realms/coldchain")


@pytest.mark.parametrize("host,port", [(S.mqtt_host, S.mqtt_port), tuple(S.kafka_bootstrap.split(":"))])
def test_broker_accepts_connections(host, port):
    with socket.create_connection((host, int(port)), timeout=5):
        pass
