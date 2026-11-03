"""I5 acceptance (Table 3.2) and test E8's SMS part: every reply command is processed correctly, and
invalid, duplicate, expired and unauthorised replies are rejected (FR9.2, Table 3.19). Each test is rolled
back.
"""
import uuid
from datetime import timedelta

import pytest

from coldchain import custody
from coldchain.incidents import store as incidents
from coldchain.notify import codes, commands
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS
from tests.test_incidents import incident, new_incident

pytestmark = pytest.mark.integration
MIN = timedelta(minutes=1)
OIC, LGA, DRIVER = "+2348000000004", "+2348000000003", "+2348000000006"


@pytest.fixture
def db(app_conn):
    return app_conn


def now(db):
    return db.execute("SELECT now()").fetchone()[0]


def sms(db, sender, text, gateway_id=None, at=None) -> commands.Result:
    return commands.handle(db, gateway_id or uuid.uuid4().hex, sender, text, at or now(db))


def no(db, inc) -> int:
    return incident(db, inc)["incident_no"]


def replies(db, user) -> list[str]:
    return [r[0] for r in db.execute("""SELECT body FROM notification WHERE recipient_id = %s AND kind = 'reply'
                                        AND created_at >= now() ORDER BY created_at, id""", (user,))]   # this test's only


def logged(db, gateway_id) -> tuple:
    return db.execute("SELECT text, outcome, reason FROM sms_inbound WHERE gateway_id = %s", (gateway_id,)).fetchone()


# ACK ------------------------------------------------------------------------------------------------------

def test_owner_acknowledges_with_one_reply(db):
    inc = new_incident(db)
    r = sms(db, OIC, f"ACK {no(db, inc)}")
    assert (r.outcome, r.changed, r.incident_id) == ("accepted", True, inc)
    assert incident(db, inc)["state"] == "acknowledged"
    channel = db.execute("""SELECT channel FROM incident_action WHERE incident_id = %s AND action = 'acknowledged'""",
                         (inc,)).fetchone()[0]
    assert channel == "sms"
    assert replies(db, USERS["oic"])[-1].startswith(f"OK #{no(db, inc)} acknowledged")


def test_duplicate_delivery_processed_once_and_repeat_command_has_no_effect(db):
    inc = new_incident(db)
    first = sms(db, OIC, f"ACK {no(db, inc)}", gateway_id="dup-1")
    again = sms(db, OIC, f"ACK {no(db, inc)}", gateway_id="dup-1")    # the gateway delivers it twice
    resent = sms(db, OIC, f"ACK {no(db, inc)}")                        # the officer sends it again
    assert (first.outcome, again.outcome, resent.outcome) == ("accepted", "duplicate", "no_effect")
    assert len(replies(db, USERS["oic"])) == 2


def test_unregistered_and_deactivated_numbers_rejected_without_reply(db):
    inc = new_incident(db)
    r = sms(db, "+2349099999999", f"ACK {no(db, inc)}", gateway_id="stranger")
    assert (r.outcome, r.reason) == ("rejected", "unregistered number")
    db.execute("UPDATE app_user SET phone_deactivated_at = now() WHERE id = %s", (USERS["oic"],))  # lost phone
    r = sms(db, OIC, f"ACK {no(db, inc)}")
    assert (r.outcome, r.reason) == ("rejected", "deactivated number")
    assert incident(db, inc)["state"] == "open" and replies(db, USERS["oic"]) == []
    assert logged(db, "stranger")[1] == "rejected"


def test_registered_user_not_entitled_is_refused(db):
    inc = new_incident(db)
    r = sms(db, DRIVER, f"ACK {no(db, inc)}")
    assert r.outcome == "rejected" and "not entitled" in r.reason
    assert incident(db, inc)["state"] == "open"
    assert replies(db, USERS["driver"])[-1].startswith("Not accepted")


def test_escalated_officer_may_acknowledge(db):
    inc = new_incident(db)
    db.execute("UPDATE incident SET current_tier = 'lga', state = 'escalated' WHERE id = %s", (inc,))
    assert sms(db, LGA, f"ACK #{no(db, inc)}").outcome == "accepted"


def test_unknown_incident_and_closed_incident(db):
    inc = new_incident(db)
    r = sms(db, OIC, "ACK 999999999")
    assert r.outcome == "rejected" and "No incident" in r.reply
    incidents.resolve(db, inc, USERS["oic"], "fixed")
    incidents.close(db, inc, USERS["lga"], "checked")
    r = sms(db, OIC, f"ACK {no(db, inc)}")
    assert (r.outcome, r.reason) == ("rejected", "incident closed") and "is closed" in r.reply


# RES --------------------------------------------------------------------------------------------------------

def test_resolution_needs_the_current_code_and_a_note(db):
    inc = new_incident(db)
    n, t = no(db, inc), now(db)
    old = codes.issue(db, USERS["oic"], t, incident_id=inc)
    code = codes.issue(db, USERS["oic"], t, incident_id=inc)          # a later SMS replaced the first code
    assert sms(db, OIC, f"RES {n} {code}").reason == "no note"
    assert sms(db, OIC, f"RES {n} {old} fixed").reason == codes.WRONG    # superseded: only the latest works
    assert sms(db, OIC, f"RES {n} ZZZ9 fixed").reason == codes.WRONG
    r = sms(db, OIC, f"RES {n} {code.lower()} moved stock to backup fridge", gateway_id="res-ok")
    assert (r.outcome, r.changed) == ("accepted", True)
    i = incident(db, inc)
    assert i["state"] == "resolved" and i["acknowledged_at"] is not None     # resolving acknowledges
    assert logged(db, "res-ok")[0] == f"RES {n} **** moved stock to backup fridge"   # code not stored
    assert sms(db, OIC, f"RES {n} {code} again").outcome == "no_effect"      # already resolved


def test_code_is_single_use_and_bound_to_its_incident(db):
    a, b = new_incident(db), new_incident(db)
    t = now(db)
    code_a = codes.issue(db, USERS["oic"], t, incident_id=a)
    codes.issue(db, USERS["oic"], t, incident_id=b)
    assert sms(db, OIC, f"RES {no(db, b)} {code_a} fixed").reason == codes.WRONG    # a's code on b
    assert sms(db, OIC, f"RES {no(db, a)} {code_a} fixed").outcome == "accepted"
    incidents.reopen(db, a, USERS["lga"], "not fixed")                # reopened by the reviewer
    assert sms(db, OIC, f"RES {no(db, a)} {code_a} fixed now").reason.startswith("no valid code")


def test_code_of_another_user_is_not_accepted(db):
    inc = new_incident(db)
    db.execute("UPDATE incident SET current_tier = 'lga' WHERE id = %s", (inc,))
    owners_code = codes.issue(db, USERS["oic"], now(db), incident_id=inc)
    codes.issue(db, USERS["lga"], now(db), incident_id=inc)
    assert sms(db, LGA, f"RES {no(db, inc)} {owners_code} fixed").reason == codes.WRONG


def test_expired_code_rejected(db):
    inc = new_incident(db)
    code = codes.issue(db, USERS["oic"], now(db) - timedelta(days=7, minutes=1), incident_id=inc)
    assert sms(db, OIC, f"RES {no(db, inc)} {code} fixed").reason == "code expired"


def test_five_wrong_codes_withdraw_the_code(db):
    inc = new_incident(db)
    code = codes.issue(db, USERS["oic"], now(db), incident_id=inc)
    for _ in range(codes.MAX_FAILED):
        assert sms(db, OIC, f"RES {no(db, inc)} ZZZ9 fixed").reason == codes.WRONG
    assert sms(db, OIC, f"RES {no(db, inc)} {code} fixed").reason.startswith("no valid code")
    assert incident(db, inc)["state"] == "open"


# Custody ----------------------------------------------------------------------------------------------------

def handoff(db):
    t = now(db) - 10 * MIN
    cid = custody.create(db, USERS["oic"], PRODUCT, 50, HEALTH_FACILITY, "Outreach", 30, None, t)
    h = custody.release(db, USERS["oic"], cid, USERS["outreach"], t)
    code = codes.issue(db, USERS["outreach"], t, handoff_id=h.id)
    cno = db.execute("SELECT consignment_no FROM consignment WHERE id = %s", (cid,)).fetchone()[0]
    return cid, h, code, cno


def test_receiver_confirms_receipt_by_sms(db):
    cid, h, code, cno = handoff(db)
    assert sms(db, OIC, f"REC {cno} {code}").outcome == "rejected"          # not the receiver
    r = sms(db, "+2348000000005", f"REC {cno} {code}")
    assert (r.outcome, r.handoff_id) == ("accepted", h.id)
    assert custody.holder(db, cid) == USERS["outreach"]
    assert sms(db, "+2348000000005", f"REC {cno} {code}").reason == "no handoff awaiting confirmation"


def test_receiver_rejects_by_sms_with_reason(db):
    cid, _, code, cno = handoff(db)
    assert sms(db, "+2348000000005", f"REJ {cno} {code}").reason == "no reason"
    assert sms(db, "+2348000000005", f"REJ {cno} K7Q9 too warm").reason == codes.WRONG
    assert sms(db, "+2348000000005", f"REJ {cno} {code} carrier warm on arrival").outcome == "accepted"
    assert custody.state(db, cid) == "rejected" and custody.holder(db, cid) == USERS["oic"]


# Other ------------------------------------------------------------------------------------------------------

def test_help_close_and_nonsense(db):
    assert sms(db, OIC, "HELP").reply.startswith("Commands:")
    assert "dashboard" in sms(db, OIC, "CLOSE 12").reply
    r = sms(db, OIC, "hello?")
    assert r.outcome == "rejected" and "HELP" in r.reply


def test_more_than_twenty_messages_an_hour_are_ignored(db):
    t = now(db) + timedelta(days=1)                                       # clear of earlier messages
    outcomes = [sms(db, OIC, "HELP", at=t + i * MIN).outcome for i in range(22)]
    assert outcomes[:20] == ["accepted"] * 20 and outcomes[20:] == ["rate_limited"] * 2
    assert sum("Too many" in r for r in replies(db, USERS["oic"])) == 1     # told once
    assert sms(db, OIC, "HELP", at=t + 82 * MIN).outcome == "accepted"      # an hour after the last
