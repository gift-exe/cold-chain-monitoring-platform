"""Verification of the tamper-evident audit trail (Section 3.4.6, FR7.2, FR7.3).

The database computes each record's hash when it is inserted (migration 0004, chain_hash). This module
recomputes the chain independently: for each record in order, the stored previous hash must equal the
hash of the record before it, and SHA-256 of that previous hash and the record's current contents must
equal the record's stored hash. An altered record fails the second check; a removed or reordered record
fails the first.

Someone able to rewrite the whole chain could recompute every hash, so the latest hash of each chain is
also copied daily to a file outside the database (an anchor). Verification checks that every anchored
hash is still in the chain at its position.

Run: uv run python -m coldchain.audit verify | anchor
"""
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from coldchain.config import get_settings

CHAINS = {"incident_action": "id", "custody_event": "seq", "admin_action": "id"}   # table: position column
ANCHOR_FILE = "audit-anchors.jsonl"


@dataclass
class Report:
    table: str
    records: int = 0
    last_position: int | None = None
    last_hash: str | None = None
    breaks: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.breaks


def _rows(conn: psycopg.Connection, table: str, pos: str):
    conn.execute("SET timezone = 'UTC'")   # timestamps must serialise exactly as when they were hashed
    with conn.cursor(name=f"verify_{table}") as cur:   # server-side cursor: the chain may be long
        cur.execute(f"""
            SELECT {pos}, prev_hash, hash,
                   jsonb_strip_nulls(to_jsonb(t) - 'prev_hash' - 'hash')::text
            FROM {table} t ORDER BY {pos}""")
        yield from cur


def verify_chain(conn: psycopg.Connection, table: str, anchors: list[dict] | None = None) -> Report:
    pos_col = CHAINS[table]
    report = Report(table)
    expected_prev = b""
    hashes: dict[int, str] = {}
    for position, prev_hash, stored, content in _rows(conn, table, pos_col):
        prev_hash, stored = bytes(prev_hash or b""), bytes(stored or b"")
        if prev_hash != expected_prev:
            report.breaks.append({"position": position, "problem": "link broken: a record before this one "
                                  "was removed, inserted or reordered"})
        if hashlib.sha256(prev_hash + content.encode()).digest() != stored:
            report.breaks.append({"position": position, "problem": "record altered after it was written"})
        expected_prev = stored
        hashes[position] = stored.hex()
        report.records += 1
        report.last_position, report.last_hash = position, stored.hex()
    for a in anchors or []:
        if a["table"] == table and hashes.get(a["position"]) != a["hash"]:
            report.breaks.append({"position": a["position"], "problem": f"anchor of {a['anchored_at']} does "
                                  "not match: the chain was rewritten or truncated"})
    return report


def load_anchors(directory: str | Path) -> list[dict]:
    path = Path(directory) / ANCHOR_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify(conn: psycopg.Connection, anchor_dir: str | Path | None = None) -> list[Report]:
    anchors = load_anchors(anchor_dir) if anchor_dir else []
    with conn.transaction():
        return [verify_chain(conn, t, anchors) for t in CHAINS]


def anchor(conn: psycopg.Connection, directory: str | Path) -> list[dict]:
    """Verify the chains, then append each chain's latest position and hash to the anchor file. Refuses to
    anchor a broken chain, which would make the break look legitimate."""
    reports = verify(conn, directory)
    broken = [r for r in reports if not r.ok]
    if broken:
        raise RuntimeError(f"not anchoring: {[(r.table, r.breaks[:3]) for r in broken]}")
    now = datetime.now(UTC).isoformat()
    lines = [{"table": r.table, "position": r.last_position, "hash": r.last_hash, "anchored_at": now}
             for r in reports if r.last_position is not None]
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    with (path / ANCHOR_FILE).open("a") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return lines


def main() -> None:
    s = get_settings()
    command = sys.argv[1] if len(sys.argv) > 1 else "verify"
    with psycopg.connect(s.db_url("app").replace("postgresql+psycopg://", "postgresql://")) as conn:
        if command == "anchor":
            for line in anchor(conn, s.audit_anchor_dir):
                print(f"anchored {line['table']} at {line['position']}: {line['hash']}")
            return
        failed = False
        for r in verify(conn, s.audit_anchor_dir):
            print(f"{r.table}: {r.records} records, {'intact' if r.ok else 'BROKEN'}"
                  f"{'' if r.last_hash is None else f', last hash {r.last_hash[:16]}…'}")
            for b in r.breaks:
                print(f"  position {b['position']}: {b['problem']}")
            failed |= not r.ok
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
