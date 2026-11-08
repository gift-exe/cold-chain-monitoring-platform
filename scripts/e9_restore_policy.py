"""Put the escalation time limits back to Table 3.15 (the values the migrations install), after the E9
evaluators shortened them on the escalation policy screen. Recorded in the audit trail as a change by the
national officer."""
import psycopg

from coldchain import admin
from coldchain.config import get_settings
from coldchain.errors import InvalidState

NATIONAL = "0b1e5a10-0000-4000-8000-000000000001"     # test.national (seeds/dev_users.sql)
# (severity, mode, kind, stage): minutes
TABLE_3_15 = {
    ("critical", "operational", "acknowledge", 1): 15, ("critical", "operational", "acknowledge", 2): 60,
    ("critical", "operational", "acknowledge", 3): 240, ("critical", "operational", "resolve", 1): 120,
    ("critical", "operational", "resolve", 2): 240, ("critical", "operational", "reminder", 1): 1440,
    ("critical", "retrospective", "acknowledge", 1): 1440, ("critical", "retrospective", "acknowledge", 2): 4320,
    ("critical", "retrospective", "resolve", 1): 10080, ("warning", "operational", "acknowledge", 1): 1440,
    ("warning", "retrospective", "acknowledge", 1): 1440,
}


def main() -> None:
    url = get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT id, severity, mode, kind, stage, delay_min FROM escalation_policy").fetchall()
        current = {(s, m, k, st): (i, d) for i, s, m, k, st, d in rows}
        changed = 0
        # Limits are cumulative, so lower ones go first when shrinking and higher ones first when growing:
        # repeat until every limit is back (each pass makes at least one change that is allowed).
        for _ in range(len(TABLE_3_15)):
            pending = [(key, mins) for key, mins in TABLE_3_15.items() if key in current and current[key][1] != mins]
            if not pending:
                break
            for key, mins in sorted(pending, key=lambda p: -p[1]):
                try:
                    with conn.transaction():
                        admin.update_policy(conn, NATIONAL, current[key][0], mins)
                except InvalidState:   # not allowed yet in this order (limits are cumulative): next pass
                    continue
                current[key] = (current[key][0], mins)
                changed += 1
        left = [k for k, m in TABLE_3_15.items() if k in current and current[k][1] != m]
        print(f"{changed} time limits restored" + (f"; could not restore {left}" if left else "; policy matches Table 3.15"))


if __name__ == "__main__":
    main()
