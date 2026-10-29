"""Load development seed data (seeds/*.sql, in name order) into the database."""
from pathlib import Path

import psycopg

from coldchain.config import get_settings

SEEDS = Path(__file__).resolve().parents[1] / "seeds"


def main() -> None:
    url = get_settings().db_url("owner").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        for f in sorted(SEEDS.glob("*.sql")):
            conn.execute(f.read_text())
            print("loaded", f.name)


if __name__ == "__main__":
    main()
