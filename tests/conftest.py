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
