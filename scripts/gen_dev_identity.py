"""Generate the development Keycloak realm and the matching app_user seed rows from one list.

Run after changing TEST_USERS:  uv run python scripts/gen_dev_identity.py
Writes infra/keycloak/coldchain-realm.json and seeds/dev_users.sql. Both are development data only.
"""
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REALM_FILE = ROOT / "infra/keycloak/coldchain-realm.json"
SEED_FILE = ROOT / "seeds/dev_users.sql"

GWALE_PHC = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d01"  # facility id from seeds/dev_reference.sql

# (id, username, full name, role, jurisdiction, facility, phone)
TEST_USERS = [
    ("0b1e5a10-0000-4000-8000-000000000001", "test.national", "Test National Officer", "national_officer", "NG", None, "+2348000000001"),
    ("0b1e5a10-0000-4000-8000-000000000002", "test.state", "Test State Officer", "state_officer", "NG-KN", None, "+2348000000002"),
    ("0b1e5a10-0000-4000-8000-000000000003", "test.lga", "Test LGA Officer", "lga_officer", "NG-KN-GWL", None, "+2348000000003"),
    ("0b1e5a10-0000-4000-8000-000000000004", "test.facility", "Test Facility OIC", "facility_oic", "NG-KN-GWL", GWALE_PHC, "+2348000000004"),
    ("0b1e5a10-0000-4000-8000-000000000005", "test.outreach", "Test Outreach Lead", "outreach_lead", "NG-KN-GWL", None, "+2348000000005"),
    ("0b1e5a10-0000-4000-8000-000000000006", "test.driver", "Test Driver", "driver", "NG-KN", None, "+2348000000006"),
    ("0b1e5a10-0000-4000-8000-000000000007", "test.admin", "Test Administrator", "admin", "NG", None, "+2348000000007"),
]
ROLES = sorted({u[3] for u in TEST_USERS})

USER_PROFILE = {
    "unmanagedAttributePolicy": "ADMIN_VIEW",
    "attributes": [
        {"name": "username", "displayName": "${username}", "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
         "validations": {"length": {"min": 3, "max": 255}}},
        {"name": "email", "displayName": "${email}", "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
         "validations": {"email": {}}},
        {"name": "firstName", "displayName": "${firstName}", "required": {"roles": ["user"]},
         "permissions": {"view": ["admin", "user"], "edit": ["admin"]}},
        {"name": "lastName", "displayName": "${lastName}", "required": {"roles": ["user"]},
         "permissions": {"view": ["admin", "user"], "edit": ["admin"]}},
        # Jurisdiction scoping for access control (NFR7); editable by administrators only
        {"name": "jurisdiction_id", "displayName": "Jurisdiction", "permissions": {"view": ["admin", "user"], "edit": ["admin"]}},
        {"name": "facility_id", "displayName": "Facility", "permissions": {"view": ["admin", "user"], "edit": ["admin"]}},
    ],
}


def attribute_mapper(name: str) -> dict:
    return {"name": name, "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {"user.attribute": name, "claim.name": name, "jsonType.label": "String",
                       "id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"}}


MAPPERS = [
    attribute_mapper("jurisdiction_id"),
    attribute_mapper("facility_id"),
    {"name": "roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
     "config": {"claim.name": "roles", "multivalued": "true", "jsonType.label": "String",
                "id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"}},
]


def realm(passwords: dict[str, str]) -> dict:
    users = []
    for uid, username, name, role, jur, fac, _ in TEST_USERS:
        first, last = name.split(" ", 1)
        attrs = {"jurisdiction_id": [jur]}
        if fac:
            attrs["facility_id"] = [fac]
        users.append({
            "id": uid, "username": username, "enabled": True, "emailVerified": True,
            "email": f"{username}@coldchain.test", "firstName": first, "lastName": last,
            "attributes": attrs, "realmRoles": [role],
            "credentials": [{"type": "password", "value": passwords[username], "temporary": False}],
        })
    return {
        "realm": "coldchain", "enabled": True, "sslRequired": "external",
        "accessTokenLifespan": 900,
        "roles": {"realm": [{"name": r} for r in ROLES]},
        "clients": [
            {"clientId": "coldchain-dashboard", "name": "Dashboard", "publicClient": True,
             "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
             "redirectUris": ["http://localhost:3000/*", "http://localhost:5173/*"],
             "webOrigins": ["http://localhost:3000", "http://localhost:5173"],
             "attributes": {"pkce.code.challenge.method": "S256",
                            "post.logout.redirect.uris": "http://localhost:3000/*##http://localhost:5173/*"},
             "protocolMappers": MAPPERS},
            # Development and test only: allows tests to obtain tokens with a username and password
            {"clientId": "coldchain-dev-cli", "name": "Development CLI (dev only)", "publicClient": True,
             "standardFlowEnabled": False, "directAccessGrantsEnabled": True, "protocolMappers": MAPPERS},
        ],
        "users": users,
        "components": {"org.keycloak.userprofile.UserProfileProvider": [{
            "providerId": "declarative-user-profile", "subComponents": {},
            "config": {"kc.user.profile.config": [json.dumps(USER_PROFILE)]}}]},
    }


def seed_sql() -> str:
    rows = []
    for uid, username, name, role, jur, fac, phone in TEST_USERS:
        f = f"'{fac}'" if fac else "NULL"
        rows.append(f"    ('{uid}', '{username}', '{name}', '{role}', '{jur}', {f}, '{phone}')")
    return ("-- Generated by scripts/gen_dev_identity.py. Development data only.\n"
            "INSERT INTO app_user (id, username, full_name, role, jurisdiction_id, facility_id, phone_number) VALUES\n"
            + ",\n".join(rows) + "\nON CONFLICT (id) DO NOTHING;\n")


def main() -> None:
    # Keep existing test passwords if the realm file already exists, so regenerating doesn't break .env copies
    old = {}
    if REALM_FILE.exists():
        for u in json.loads(REALM_FILE.read_text())["users"]:
            old[u["username"]] = u["credentials"][0]["value"]
    passwords = {u[1]: old.get(u[1]) or secrets.token_urlsafe(12) for u in TEST_USERS}
    REALM_FILE.parent.mkdir(parents=True, exist_ok=True)
    REALM_FILE.write_text(json.dumps(realm(passwords), indent=2) + "\n")
    SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEED_FILE.write_text(seed_sql())
    print("wrote", REALM_FILE.relative_to(ROOT), "and", SEED_FILE.relative_to(ROOT))


if __name__ == "__main__":
    main()
