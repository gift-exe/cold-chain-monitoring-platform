"""Load development seed data (seeds/*.sql, in name order) into the database.

--no-demo skips the demo network (the evaluation stack has only the simulated Kano network)."""
import sys
from pathlib import Path

import psycopg

from coldchain.config import get_settings

SEEDS = Path(__file__).resolve().parents[1] / "seeds"


def main() -> None:
    url = get_settings().db_url("owner").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        for f in sorted(SEEDS.glob("*.sql")):
            if "--no-demo" in sys.argv and "demo" in f.name:
                continue
            conn.execute(f.read_text())
            print("loaded", f.name)


if __name__ == "__main__":
    main()
