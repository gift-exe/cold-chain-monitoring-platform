"""FR11.1-FR11.2: administrators manage users, jurisdictions, facilities and devices; national officers manage
the escalation policy (Table 3.21). Every change is restricted to its role and recorded in the audit trail.
Each test is rolled back."""
import uuid

import pytest

from coldchain import admin
from coldchain.errors import InvalidState, NotPermitted
from coldchain.incidents.store import load_policy
from tests.conftest import HEALTH_FACILITY, USERS

pytestmark = pytest.mark.integration
ADMIN, NATIONAL = USERS["admin"], USERS["national"]


@pytest.fixture
def db(app_conn):
    return app_conn


def recorded(db, action: str, target: str) -> bool:
    return db.execute("SELECT count(*) FROM admin_action WHERE action = %s AND target_id = %s",
                      (action, target)).fetchone()[0] == 1


def test_administrator_adds_and_changes_users(db):
    u = admin.create_user(db, ADMIN, {"username": f"oic.{uuid.uuid4().hex[:6]}", "full_name": "New OIC",
                                      "role": "facility_oic", "jurisdiction_id": "NG-KN-GWL",
                                      "facility_id": HEALTH_FACILITY, "phone_number": "+2348000001234"})
    assert recorded(db, "user_created", u["id"])
    admin.update_user(db, ADMIN, u["id"], {"role": "lga_officer", "facility_id": None})
    assert db.execute("SELECT role FROM app_user WHERE id = %s", (u["id"],)).fetchone()[0] == "lga_officer"
    admin.update_user(db, ADMIN, u["id"], {"active": False})
    assert db.execute("SELECT active FROM app_user WHERE id = %s", (u["id"],)).fetchone()[0] is False
    assert db.execute("SELECT count(*) FROM admin_action WHERE target_id = %s", (u["id"],)).fetchone()[0] == 3


@pytest.mark.parametrize("bad", [
    {"role": "state_officer", "jurisdiction_id": "NG-KN-GWL"},        # a state officer belongs to a state
    {"role": "facility_oic", "jurisdiction_id": "NG-KN-GWL"},         # an officer-in-charge needs a facility
    {"role": "pilot", "jurisdiction_id": "NG"},
    {"role": "lga_officer", "jurisdiction_id": "NG-KN-GWL", "phone_number": "08031234567"},
])
def test_inconsistent_users_refused(db, bad):
    with pytest.raises(InvalidState):
        admin.create_user(db, ADMIN, {"username": f"x.{uuid.uuid4().hex[:6]}", "full_name": "X", **bad})


def test_administrator_cannot_lock_themselves_out(db):
    with pytest.raises(InvalidState):
        admin.update_user(db, ADMIN, ADMIN, {"active": False})


def test_jurisdictions_facilities_and_devices(db):
    code = f"NG-KN-Z{uuid.uuid4().hex[:2].upper()}"
    admin.create_jurisdiction(db, ADMIN, {"id": code, "level": "lga", "name": "New LGA", "parent_id": "NG-KN"})
    with pytest.raises(InvalidState):      # an LGA belongs to a state
        admin.create_jurisdiction(db, ADMIN, {"id": "NG-ZZ1", "level": "lga", "name": "X", "parent_id": "NG"})
    f = admin.create_facility(db, ADMIN, {"name": "New PHC", "tier": "health_facility", "jurisdiction_id": code,
                                          "power_source": "solar", "lat": 12.0, "lon": 8.5})
    admin.update_facility(db, ADMIN, f["id"], {"power_source": "grid", "lat": 12.1, "ward": "Dorayi"})
    assert db.execute("SELECT ward FROM facility WHERE id = %s", (f["id"],)).fetchone()[0] == "Dorayi"
    serial = f"ADM-{uuid.uuid4().hex[:8]}"
    d = admin.register_device(db, ADMIN, {"serial_no": serial, "type": "fixed", "equipment_type": "ice_lined_refrigerator",
                                          "facility_id": f["id"], "has_power_signal": True})
    admin.update_device(db, ADMIN, serial, {"status": "retired"})
    assert db.execute("SELECT status FROM device WHERE serial_no = %s", (serial,)).fetchone()[0] == "retired"
    with pytest.raises(InvalidState):      # a fixed device belongs to a facility
        admin.register_device(db, ADMIN, {"serial_no": f"ADM-{uuid.uuid4().hex[:8]}", "type": "fixed",
                                          "equipment_type": "ice_lined_refrigerator"})
    for action, target in [("jurisdiction_created", code), ("facility_created", f["id"]),
                           ("facility_updated", f["id"]), ("device_registered", d["id"]), ("device_updated", d["id"])]:
        assert recorded(db, action, target), action


def test_national_officer_changes_escalation_limits_in_order(db):
    steps = {(p["severity"], p["mode"], p["kind"], p["stage"]): p for p in admin.policy(db)}
    lga = steps[("critical", "operational", "acknowledge", 1)]
    admin.update_policy(db, NATIONAL, lga["id"], 20)
    assert recorded(db, "policy_updated", str(lga["id"]))
    assert any(s.delay.total_seconds() == 20 * 60 for s in load_policy(db).stages("critical", "operational", "acknowledge"))
    with pytest.raises(InvalidState):      # cumulative: the LGA limit cannot pass the state limit (60 min)
        admin.update_policy(db, NATIONAL, lga["id"], 90)


@pytest.mark.parametrize("who", ["state", "lga", "oic", "outreach", "driver", "national"])
def test_only_administrators_manage_users_facilities_and_devices(db, who):
    for fn in (lambda: admin.create_user(db, USERS[who], {"username": "zz.zz", "full_name": "Z", "role": "driver",
                                                          "jurisdiction_id": "NG-KN"}),
               lambda: admin.create_facility(db, USERS[who], {"name": "Z", "tier": "health_facility",
                                                              "jurisdiction_id": "NG-KN", "power_source": "grid",
                                                              "lat": 12, "lon": 8}),
               lambda: admin.update_device(db, USERS[who], "nothing", {"status": "retired"}),
               lambda: admin.rename_jurisdiction(db, USERS[who], "NG-KN", "Z")):
        with pytest.raises(NotPermitted):
            fn()


@pytest.mark.parametrize("who", ["admin", "state", "lga", "oic"])
def test_only_national_officers_change_the_escalation_policy(db, who):
    with pytest.raises(NotPermitted):
        admin.update_policy(db, USERS[who], admin.policy(db)[0]["id"], 5)
