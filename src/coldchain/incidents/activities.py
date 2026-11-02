"""Temporal activities: the steps of the incident and handoff workflows that touch the database.

Each activity is given the workflow's notion of the current time, so that escalation decisions follow
the workflow's durable timers (and can be tested with Temporal's time-skipping test server).
"""
from dataclasses import dataclass
from datetime import datetime

import psycopg
from temporalio import activity

from coldchain import audit, custody
from coldchain.config import get_settings
from coldchain.incidents import store


@dataclass
class Due:
    next_at: str | None    # ISO 8601; None if nothing is pending
    finished: bool


def db_url() -> str:
    return get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://")


class Activities:
    def __init__(self, url: str | None = None, anchor_dir: str | None = None):
        self.url = url or db_url()
        self.anchor_dir = anchor_dir or get_settings().audit_anchor_dir

    @activity.defn
    def apply_due(self, incident_id: str, now: str) -> Due:
        with psycopg.connect(self.url, autocommit=True) as conn, conn.transaction():
            next_at, finished = store.apply_due(conn, incident_id, datetime.fromisoformat(now))
        return Due(next_at.isoformat() if next_at else None, finished)

    @activity.defn
    def expire_handoff(self, handoff_id: str, now: str) -> str | None:
        with psycopg.connect(self.url, autocommit=True) as conn, conn.transaction():
            return custody.expire(conn, handoff_id, datetime.fromisoformat(now))

    @activity.defn
    def verify_and_anchor(self) -> list[dict]:
        with psycopg.connect(self.url) as conn:
            return audit.anchor(conn, self.anchor_dir)
