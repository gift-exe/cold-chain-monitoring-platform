"""Escalation policy (Table 3.15): who is notified, and when, for each kind of incident and response.

Pure tests of incidents.policy: an incident's clock is advanced step by step through simulated time,
with acknowledgements at chosen moments. Times are minutes from the incident's creation.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from coldchain.incidents.policy import Clock, Policy, advance, next_step

# The default policy (migrations 0001 and 0004), i.e. Table 3.15
POLICY = Policy([
    ("critical", "operational", "acknowledge", 0, "owner", 0),
    ("critical", "operational", "acknowledge", 1, "lga", 15),
    ("critical", "operational", "acknowledge", 2, "state", 60),
    ("critical", "operational", "acknowledge", 3, "national", 240),
    ("critical", "operational", "resolve", 1, "next", 120),
    ("critical", "operational", "resolve", 2, "next", 240),
    ("critical", "operational", "reminder", 1, "national", 1440),
    ("critical", "retrospective", "acknowledge", 0, "owner", 0),
    ("critical", "retrospective", "acknowledge", 1, "lga", 1440),
    ("critical", "retrospective", "acknowledge", 2, "state", 4320),
    ("critical", "retrospective", "resolve", 1, "state", 10080),
    ("warning", "operational", "acknowledge", 0, "owner", 0),
    ("warning", "operational", "acknowledge", 1, "lga", 1440),
    ("warning", "retrospective", "acknowledge", 0, "owner", 0),
    ("warning", "retrospective", "acknowledge", 1, "lga", 1440),
])
T0 = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
DAY = 1440


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def new(severity="critical", mode="operational") -> Clock:
    return Clock(severity, mode, "open", "owner", False, T0, -1, None, 0, None, False, None)


def acknowledge(c: Clock, minute: float) -> Clock:
    return replace(c, acknowledged=True, state="acknowledged", resolve_due_from=at(minute), resolve_stage=0)


def simulate(c: Clock, until: float, ack_at: float | None = None, resolve_at: float | None = None):
    """Steps applied up to `until`, as (minute, kind, tier); 'skip' steps are omitted."""
    out = []
    while True:
        step = next_step(c, POLICY)
        events = [(m, f) for m, f in ((ack_at, "ack"), (resolve_at, "resolve")) if m is not None]
        pending = [(m, f) for m, f in events if (step is None or at(m) <= step.at) and m <= until]
        if pending:
            m, f = min(pending)
            c = acknowledge(c, m) if f == "ack" else replace(c, state="resolved")
            ack_at = None if f == "ack" else ack_at
            resolve_at = None if f == "resolve" else resolve_at
            continue
        if step is None or step.at > at(until):
            return out, c
        if step.kind != "skip":
            out.append(((step.at - T0) / timedelta(minutes=1), step.kind, step.tier))
        c = advance(c, step, step.at, POLICY)


def test_critical_never_acknowledged_reaches_national_at_4_hours_then_overdue_with_daily_reminders():
    steps, c = simulate(new(), until=3 * DAY + 60)
    assert steps == [(0, "notify", "owner"), (15, "escalate", "lga"), (60, "escalate", "state"),
                     (240, "escalate", "national"), (240 + DAY, "overdue", "national"),
                     (240 + 2 * DAY, "reminder", "national")]
    assert c.overdue and c.current_tier == "national"


def test_critical_acknowledged_in_time_escalates_only_if_unresolved_two_then_four_hours_later():
    steps, c = simulate(new(), until=3 * DAY, ack_at=10)
    assert steps == [(0, "notify", "owner"), (130, "escalate", "lga"), (250, "escalate", "state")]
    assert not c.overdue   # Table 3.15: overdue applies at the national tier only


def test_acknowledgement_cancels_pending_acknowledgement_escalations():
    steps, _ = simulate(new(), until=200, ack_at=14.9)
    assert (15, "escalate", "lga") not in steps and (60, "escalate", "state") not in steps


def test_late_acknowledgement_keeps_escalation_and_resolution_goes_above_highest_tier():
    """Acknowledged at 20 min, after escalation to the LGA at 15: the LGA escalation stands, and the
    resolution time limit escalates to the tier above the highest already notified (state)."""
    steps, _ = simulate(new(), until=260 + DAY + 60, ack_at=20)
    assert steps == [(0, "notify", "owner"), (15, "escalate", "lga"), (140, "escalate", "state"),
                     (260, "escalate", "national"), (260 + DAY, "overdue", "national")]


def test_acknowledged_at_national_tier_is_overdue_when_resolution_limit_passes():
    steps, _ = simulate(new(), until=300 + 120 + DAY, ack_at=300)
    assert steps[-2:] == [(420, "overdue", "national"), (420 + DAY, "reminder", "national")]


def test_resolved_incident_has_no_further_steps():
    steps, _ = simulate(new(), until=3 * DAY, ack_at=5, resolve_at=50)
    assert steps == [(0, "notify", "owner")]
    assert next_step(replace(new(), state="closed"), POLICY) is None


def test_retrospective_critical_uses_slow_timers_and_no_reminders():
    steps, c = simulate(new(mode="retrospective"), until=10 * DAY)
    assert steps == [(0, "notify", "owner"), (DAY, "escalate", "lga"), (3 * DAY, "escalate", "state"),
                     (4 * DAY, "overdue", "state")]
    assert next_step(c, POLICY) is None


def test_retrospective_acknowledged_goes_to_state_after_seven_day_review_limit():
    steps, _ = simulate(new(mode="retrospective"), until=20 * DAY, ack_at=100)
    assert steps == [(0, "notify", "owner"), (100 + 7 * DAY, "escalate", "state"),
                     (100 + 8 * DAY, "overdue", "state")]


def test_warning_goes_to_lga_after_24_hours_and_is_never_overdue():
    steps, c = simulate(new(severity="warning"), until=30 * DAY)
    assert steps == [(0, "notify", "owner"), (DAY, "escalate", "lga")]
    assert not c.overdue
    steps, _ = simulate(new(severity="warning"), until=30 * DAY, ack_at=60)
    assert steps == [(0, "notify", "owner")]


def test_recurrence_renotifies_owner_and_measures_again_from_reopening():
    """A recurrence reopens an incident already escalated to the LGA: the owner is notified again, the
    LGA stage is not repeated, and the state tier follows 60 minutes after reopening."""
    reopened = replace(new(), state="open", current_tier="lga", ack_due_from=at(500), ack_stage=-1)
    steps, _ = simulate(reopened, until=600)
    assert steps == [(500, "notify", "owner"), (560, "escalate", "state")]


def test_reviewer_reopening_restarts_resolution_limits():
    c = replace(acknowledge(new(), 10), ack_stage=0, resolve_stage=0, current_tier="lga",
                state="acknowledged", resolve_due_from=at(1000))   # as store.reopen leaves it
    steps, _ = simulate(c, until=1300)
    assert steps == [(1120, "escalate", "state"), (1240, "escalate", "national")]


def test_catch_up_after_outage_applies_missed_steps_in_order():
    """If the service was down from minute 5 to minute 100, every step due meanwhile is applied on
    restart, in the order the policy requires."""
    c = new()
    applied = []
    for now in (at(5), at(100)):
        while (step := next_step(c, POLICY)) is not None and step.at <= now:
            applied.append((now, step.tier))
            c = advance(c, step, now, POLICY)
    assert [t for _, t in applied] == ["owner", "lga", "state"]
    assert all(n == at(100) for n, _ in applied[1:])
