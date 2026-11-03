"""One-time response codes for SMS commands that change responsibility (Table 3.19): RES for incidents,
REC and REJ for handoffs.

Each SMS that invites such a reply carries a fresh code for its recipient; issuing one voids that
recipient's earlier codes for the same incident or handoff, so only the code in the latest message works.
A code is single-use, valid for one incident or handoff, expires after 7 days, and is voided when the
incident is resolved or closed or the handoff settled. Only an HMAC of the code is stored. Five wrong
codes for the same incident or handoff void the recipient's code, so a short code cannot be guessed
by trying values (the reply then points the user to the dashboard).
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

import psycopg

from coldchain.config import get_settings

ALPHABET = "ACDEFHJKMNPRTUVWXY3479"   # no letters and digits that are easily confused (0/O, 1/I/L, 5/S...)
LENGTH = 4                            # 22^4 = 234,256 values (fewer: each has a digit)
VALID_FOR = timedelta(days=7)
MAX_FAILED = 5
WRONG = "wrong code"


def _hash(code: str) -> str:
    return hmac.new(get_settings().code_secret.encode(), code.upper().encode(), hashlib.sha256).hexdigest()


def _subject(incident_id: str | None, handoff_id: str | None) -> tuple[str, str]:
    return ("incident_id", incident_id) if incident_id else ("handoff_id", handoff_id)


def issue(conn: psycopg.Connection, user_id: str, now: datetime, *, incident_id: str | None = None,
          handoff_id: str | None = None) -> str:
    column, subject = _subject(incident_id, handoff_id)
    conn.execute(f"""UPDATE response_code SET voided_at = %s WHERE user_id = %s AND {column} = %s
                     AND used_at IS NULL AND voided_at IS NULL""", (now, user_id, subject))
    code = ""
    while not any(c.isdigit() for c in code):   # a digit keeps codes distinct from words such as HEAT
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    conn.execute(f"""INSERT INTO response_code (user_id, {column}, code_hash, issued_at, expires_at)
                     VALUES (%s, %s, %s, %s, %s)""", (user_id, subject, _hash(code), now, now + VALID_FOR))
    return code


def check(conn: psycopg.Connection, user_id: str, code: str, now: datetime, *, incident_id: str | None = None,
          handoff_id: str | None = None) -> str | None:
    """Use the code if it is the user's current code. Returns None if accepted, otherwise the reason it
    was not. The caller's transaction decides: a code is only marked used if the command succeeds."""
    column, subject = _subject(incident_id, handoff_id)
    row = conn.execute(f"""SELECT id, code_hash, expires_at FROM response_code
                           WHERE user_id = %s AND {column} = %s AND used_at IS NULL AND voided_at IS NULL
                           ORDER BY issued_at DESC LIMIT 1 FOR UPDATE""", (user_id, subject)).fetchone()
    if row is None:
        return "no valid code: it has been used, replaced or withdrawn"
    code_id, stored, expires = row
    if now >= expires:
        conn.execute("UPDATE response_code SET voided_at = %s WHERE id = %s", (now, code_id))
        return "code expired"
    if not hmac.compare_digest(stored, _hash(code)):
        return WRONG             # the caller records it with failed(), outside the command's savepoint
    conn.execute("UPDATE response_code SET used_at = %s WHERE id = %s", (now, code_id))
    return None


def failed(conn: psycopg.Connection, user_id: str, now: datetime, *, incident_id: str | None = None,
           handoff_id: str | None = None) -> None:
    """Count a wrong code against the user's current code; the fifth voids it."""
    column, subject = _subject(incident_id, handoff_id)
    conn.execute(f"""UPDATE response_code SET failed_attempts = failed_attempts + 1,
                            voided_at = CASE WHEN failed_attempts + 1 >= %s THEN %s END
                     WHERE user_id = %s AND {column} = %s AND used_at IS NULL AND voided_at IS NULL""",
                 (MAX_FAILED, now, user_id, subject))


def void(conn: psycopg.Connection, now: datetime | None = None, *, incident_id: str | None = None,
         handoff_id: str | None = None) -> None:
    """Withdraw every outstanding code for an incident (resolved, closed) or handoff (settled)."""
    column, subject = _subject(incident_id, handoff_id)
    conn.execute(f"""UPDATE response_code SET voided_at = coalesce(%s::timestamptz, now()) WHERE {column} = %s
                     AND used_at IS NULL AND voided_at IS NULL""", (now, subject))
