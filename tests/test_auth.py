"""I1 acceptance: test users of each role sign in and receive the correct role and jurisdiction claims."""
import base64
import json

import httpx
import pytest

from coldchain.config import get_settings

pytestmark = pytest.mark.integration
S = get_settings()
TOKEN_URL = f"{S.keycloak_url}/realms/{S.keycloak_realm}/protocol/openid-connect/token"


def password_grant(client_id: str, username: str, password: str) -> httpx.Response:
    return httpx.post(TOKEN_URL, timeout=10, data={
        "grant_type": "password", "client_id": client_id, "username": username, "password": password,
        "scope": "openid"})


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def test_every_role_signs_in_with_correct_claims(realm_users, owner_conn):
    for u in realm_users:
        r = password_grant("coldchain-dev-cli", u["username"], u["credentials"][0]["value"])
        assert r.status_code == 200, f"{u['username']}: {r.text}"
        c = claims(r.json()["access_token"])

        role, jurisdiction, facility = owner_conn.execute(
            "SELECT role, jurisdiction_id, facility_id FROM app_user WHERE id = %s", (c["sub"],)).fetchone()
        assert c["roles"] == [role] or role in c["roles"], u["username"]
        assert c["jurisdiction_id"] == jurisdiction, u["username"]
        assert c.get("facility_id") == (str(facility) if facility else None), u["username"]


def test_wrong_password_rejected(realm_users):
    r = password_grant("coldchain-dev-cli", realm_users[0]["username"], "not-the-password")
    assert r.status_code == 401


def test_dashboard_client_does_not_allow_password_grant(realm_users):
    u = realm_users[0]
    r = password_grant("coldchain-dashboard", u["username"], u["credentials"][0]["value"])
    assert r.status_code in (400, 401)
