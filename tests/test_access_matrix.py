"""E8 access control (Table 3.10): every role against every jurisdiction and action.

The expected permissions are written out from Tables 3.15 and 3.21, independently of the code that enforces
them, and every combination is attempted:

- roles: the seven development users (one per role, Gwale LGA where the role has an LGA);
- incidents: at the officer-in-charge's own facility, at another Gwale facility, and in Dala LGA, each at
  every escalation tier (not escalated, LGA, state, national);
- actions: view the incident, acknowledge, resolve, and (once the owner has resolved it) close; and the
  administrative actions (product limits, phone numbers, user list, system status).

Each attempt runs in a savepoint that is rolled back, so attempts do not affect one another. The test fails
on any difference; the full matrix is written to runs/eval/e8/access_matrix.json for Chapter Five.
"""
import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from coldchain import admin
from coldchain.api import views
from coldchain.errors import InvalidState, NotPermitted
from coldchain.incidents import store
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS
from tests.test_incidents import event, now

pytestmark = pytest.mark.integration
MIN = timedelta(minutes=1)
OTHER_GWALE = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e12"      # demo network (seeds/dev_z_demo_network.sql)
DALA = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e21"
PLACES = {"own_facility": HEALTH_FACILITY, "other_gwale_facility": OTHER_GWALE, "dala": DALA}
TIERS = {"owner": 0, "lga": 16, "state": 61, "national": 241}   # minutes after creation (Table 3.15)
RANK = ["owner", "lga", "state", "national"]
OUT = Path(__file__).resolve().parents[1] / "runs" / "eval" / "e8"


def expected_view(role: str, place: str, owner: str, who: str) -> bool:
    """Table 3.21, incident view."""
    if USERS[who] == owner:
        return True
    return {"national": True, "state": True, "lga": place != "dala", "oic": place == "own_facility",
            "outreach": False, "driver": False, "admin": False}[role]


def expected_act(role: str, place: str, tier: str, owner: str, who: str) -> bool:
    """Table 3.15: the owner, or an officer of a tier the incident has been escalated to, whose
    jurisdiction covers it."""
    if USERS[who] == owner:
        return True
    reached = RANK.index(tier)
    return {"lga": reached >= 1 and place != "dala", "state": reached >= 2, "national": reached >= 3}.get(role, False)


def expected_close(role: str, place: str, owner: str, who: str) -> bool:
    """Table 3.15: a reviewing officer at LGA level or above, covering the incident, who did not resolve it
    (the owner resolves it here)."""
    if USERS[who] == owner:
        return False
    return {"lga": place != "dala", "state": True, "national": True}.get(role, False)


def attempt(db, fn) -> bool:
    try:
        with db.transaction(force_rollback=True):
            fn()
        return True
    except NotPermitted:
        return False


@pytest.fixture
def db(app_conn):
    return app_conn


def incident_at(db, facility: str, tier: str) -> tuple[str, str]:
    dev = db.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                        VALUES (%s, %s, 'fixed', 'test_fridge') RETURNING id::text""",
                     (f"E8-{uuid.uuid4().hex[:10]}", facility)).fetchone()[0]
    inc = store.on_event(db, event(db, dev)).incident_id
    store.apply_due(db, inc, now(db) + TIERS[tier] * MIN)
    current, owner = db.execute("SELECT current_tier, owner_id::text FROM incident WHERE id = %s", (inc,)).fetchone()
    assert current == tier
    return inc, owner


def test_access_matrix(db):
    rows, wrong = [], []

    def check(who, place, tier, action, expected, allowed):
        rows.append({"role": who, "incident": place, "tier": tier, "action": action, "expected": expected,
                     "allowed": allowed})
        if expected != allowed:
            wrong.append(rows[-1])

    for place, facility in PLACES.items():
        for tier in TIERS:
            inc, owner = incident_at(db, facility, tier)
            for who in USERS:
                v = views.viewer(db, USERS[who])
                check(who, place, tier, "view", expected_view(who, place, owner, who),
                      attempt(db, lambda v=v, inc=inc: views.incident(db, v, inc)))
                check(who, place, tier, "acknowledge", expected_act(who, place, tier, owner, who),
                      attempt(db, lambda who=who, inc=inc: store.acknowledge(db, inc, USERS[who])))
                check(who, place, tier, "resolve", expected_act(who, place, tier, owner, who),
                      attempt(db, lambda who=who, inc=inc: store.resolve(db, inc, USERS[who], "e8")))

                def close_after_owner_resolves(who=who, inc=inc, owner=owner):
                    store.resolve(db, inc, owner, "e8")
                    try:
                        store.close(db, inc, USERS[who], "e8")
                    except InvalidState:
                        raise NotPermitted("not resolved") from None
                check(who, place, tier, "close", expected_close(who, place, owner, who),
                      attempt(db, close_after_owner_resolves))

    for who in USERS:
        role_admin, role_national = who == "admin", who == "national"
        check(who, "-", "-", "change product limits", role_national,
              attempt(db, lambda who=who: admin.update_product(db, USERS[who], PRODUCT, {"upper_limit": 8.5})))
        check(who, "-", "-", "change phone number", role_admin,
              attempt(db, lambda who=who: admin.set_phone(db, USERS[who], "test.driver", "+2348000000099")))
        check(who, "-", "-", "list users", role_admin, attempt(db, lambda who=who: admin.users(db, USERS[who])))
        check(who, "-", "-", "system status", role_admin,
              attempt(db, lambda who=who: admin.system_status(db, USERS[who])))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "access_matrix.json").write_text(json.dumps({
        "attempts": len(rows), "permitted_but_not_entitled": sum(r["allowed"] and not r["expected"] for r in wrong),
        "refused_but_entitled": sum(r["expected"] and not r["allowed"] for r in wrong), "differences": wrong,
        "rows": rows}, indent=1))
    assert wrong == []
