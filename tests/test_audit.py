"""I4 acceptance (Table 3.2): audit verification detects an altered record (FR7.2, FR7.3).

The tampering is done as the schema owner, who can disable the append-only trigger; that is the
strongest attacker inside the database short of a superuser. Everything is rolled back.
"""
import pytest

from coldchain import audit
from tests.test_incidents import new_incident

pytestmark = pytest.mark.integration


def recent_actions(conn, n=3) -> list[int]:
    inc = new_incident(conn)
    for i in range(n - 1):
        conn.execute("INSERT INTO incident_action (incident_id, action, channel, details) "
                     "VALUES (%s, 'action_recorded', 'dashboard', %s)", (inc, f'{{"note": "step {i}"}}'))
    return [r[0] for r in conn.execute("SELECT id FROM incident_action WHERE incident_id = %s ORDER BY id", (inc,))]


def report(conn, anchors=None) -> audit.Report:
    return audit.verify_chain(conn, "incident_action", anchors)


def unlocked(conn):
    conn.execute("ALTER TABLE incident_action DISABLE TRIGGER incident_action_no_update_delete")


def test_intact_chain_verifies(owner_conn):
    recent_actions(owner_conn)
    r = report(owner_conn)
    assert r.ok and r.records >= 3
    assert all(c.ok for c in audit.verify(owner_conn))


def test_altered_record_detected(owner_conn):
    ids = recent_actions(owner_conn)
    unlocked(owner_conn)
    owner_conn.execute("""UPDATE incident_action SET details = '{"note": "nothing happened"}' WHERE id = %s""",
                       (ids[1],))
    r = report(owner_conn)
    assert not r.ok
    assert r.breaks == [{"position": ids[1], "problem": "record altered after it was written"}]


def test_altered_actor_or_time_detected(owner_conn):
    ids = recent_actions(owner_conn)
    unlocked(owner_conn)
    owner_conn.execute("UPDATE incident_action SET occurred_at = occurred_at - interval '3 hours' WHERE id = %s",
                       (ids[2],))
    assert [b["position"] for b in report(owner_conn).breaks] == [ids[2]]


def test_removed_record_detected(owner_conn):
    ids = recent_actions(owner_conn)
    unlocked(owner_conn)
    owner_conn.execute("DELETE FROM incident_action WHERE id = %s", (ids[1],))
    r = report(owner_conn)
    assert r.breaks == [{"position": ids[2], "problem": "link broken: a record before this one was removed, "
                                                       "inserted or reordered"}]


def test_rewritten_chain_detected_by_anchor(owner_conn, tmp_path):
    """An attacker who alters a record and recomputes every later hash leaves a self-consistent chain;
    the hash anchored outside the database no longer matches."""
    ids = recent_actions(owner_conn)
    anchored = audit.anchor(owner_conn, tmp_path)
    assert "incident_action" in {a["table"] for a in anchored}   # empty chains have nothing to anchor
    unlocked(owner_conn)
    owner_conn.execute("SET timezone = 'UTC'")
    owner_conn.execute("""UPDATE incident_action SET details = '{"note": "nothing happened"}' WHERE id = %s""",
                       (ids[1],))
    owner_conn.execute("""
        DO $$ DECLARE r record; prev bytea; BEGIN
            SELECT hash INTO prev FROM incident_action WHERE id < %(first)s ORDER BY id DESC LIMIT 1;
            FOR r IN SELECT id FROM incident_action WHERE id >= %(first)s ORDER BY id LOOP
                UPDATE incident_action t SET prev_hash = prev, hash = sha256(prev || convert_to(
                    jsonb_strip_nulls(to_jsonb(t) - 'prev_hash' - 'hash')::text, 'UTF8'))
                WHERE id = r.id RETURNING hash INTO prev;
            END LOOP;
        END $$""".replace("%(first)s", str(ids[1])))
    assert report(owner_conn).ok                       # the rewrite is internally consistent...
    r = report(owner_conn, audit.load_anchors(tmp_path))
    assert not r.ok and "anchor" in r.breaks[0]["problem"]   # ...but not with the anchor


def test_broken_chain_is_not_anchored(owner_conn, tmp_path):
    ids = recent_actions(owner_conn)
    unlocked(owner_conn)
    owner_conn.execute("DELETE FROM incident_action WHERE id = %s", (ids[0],))
    with pytest.raises(RuntimeError, match="not anchoring"):
        audit.anchor(owner_conn, tmp_path)
    assert audit.load_anchors(tmp_path) == []
