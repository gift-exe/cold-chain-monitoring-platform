"""Escalation policy (Table 3.15) as a pure function of an incident's escalation clock.

next_step() answers one question: given what has already happened to an incident, what is the next
thing the policy requires, and when? The incident workflow sleeps until that time, applies the step and
asks again; acknowledgement, resolution and reopening change the clock and so the answer. Because the
answer depends only on stored facts, it is the same however often it is asked, and after an outage every
step that fell due is applied in order (catch-up).

Interpretations of Table 3.15 made here (prototype assumptions, to be confirmed):
- Acknowledgement stages are measured from creation; after a recurrence reopens an incident, the owner
  is re-notified and the stages are measured again from that time.
- Resolution stages are measured from acknowledgement, or from a reviewer's reopening. A stage whose tier
  is 'next' goes to the tier above the highest tier already notified.
- Only critical incidents become overdue. An incident is overdue when a resolution deadline falls due
  and there is no higher tier to escalate to, or when it has been at the top tier of its policy for one
  reminder interval (default 24 hours) without being resolved. Overdue incidents receive reminders at
  that interval if the policy defines one (operational critical incidents only, Table 3.15).
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

TIERS = ("owner", "lga", "state", "national")
DEFAULT_GRACE = timedelta(hours=24)


def rank(tier: str) -> int:
    return TIERS.index(tier)


def above(tier: str) -> str | None:
    i = rank(tier) + 1
    return TIERS[i] if i < len(TIERS) else None


@dataclass(frozen=True)
class Stage:
    kind: str          # acknowledge | resolve | reminder
    stage: int
    tier: str          # owner | lga | state | national | next
    delay: timedelta   # from the start of the relevant clock


class Policy:
    def __init__(self, rows: list[tuple[str, str, str, int, str, int]]):
        """rows: (severity, mode, kind, stage, tier, delay_min), as stored in escalation_policy."""
        self._stages: dict[tuple[str, str, str], list[Stage]] = {}
        for severity, mode, kind, stage, tier, delay_min in rows:
            self._stages.setdefault((severity, mode, kind), []).append(
                Stage(kind, stage, tier, timedelta(minutes=delay_min)))
        for stages in self._stages.values():
            stages.sort(key=lambda s: s.stage)

    def stages(self, severity: str, mode: str, kind: str) -> list[Stage]:
        return self._stages.get((severity, mode, kind), [])

    def top_tier(self, severity: str, mode: str) -> str:
        tiers = [s.tier for s in self.stages(severity, mode, "acknowledge")] or ["owner"]
        return max(tiers, key=rank)

    def reminder(self, severity: str, mode: str) -> timedelta | None:
        r = self.stages(severity, mode, "reminder")
        return r[0].delay if r else None


@dataclass(frozen=True)
class Clock:
    """The facts about an incident that the policy depends on."""
    severity: str
    mode: str
    state: str
    current_tier: str
    acknowledged: bool
    ack_due_from: datetime
    ack_stage: int                 # last acknowledgement stage applied; -1 before the owner is notified
    resolve_due_from: datetime | None
    resolve_stage: int
    top_tier_at: datetime | None
    overdue: bool
    last_reminder_at: datetime | None


@dataclass(frozen=True)
class Step:
    at: datetime
    kind: str          # notify | escalate | skip | overdue | reminder
    tier: str
    stage: int
    clock: str         # acknowledge | resolve | top: which clock the step belongs to


def next_step(c: Clock, policy: Policy) -> Step | None:
    if c.state in ("resolved", "closed"):
        return None
    top = policy.top_tier(c.severity, c.mode)
    grace = policy.reminder(c.severity, c.mode) or DEFAULT_GRACE

    if c.overdue:
        interval = policy.reminder(c.severity, c.mode)
        if interval is None or c.last_reminder_at is None:
            return None
        return Step(c.last_reminder_at + interval, "reminder", top, 0, "top")

    if not c.acknowledged:
        for s in policy.stages(c.severity, c.mode, "acknowledge"):
            if s.stage > c.ack_stage:
                at = c.ack_due_from + s.delay
                if s.stage == 0:
                    return Step(at, "notify", "owner", 0, "acknowledge")
                # a tier already reached (e.g. after a late acknowledgement was withdrawn) is skipped
                kind = "escalate" if rank(s.tier) > rank(c.current_tier) else "skip"
                return Step(at, kind, s.tier, s.stage, "acknowledge")
    elif c.resolve_due_from is not None:
        for s in policy.stages(c.severity, c.mode, "resolve"):
            if s.stage > c.resolve_stage:
                at = c.resolve_due_from + s.delay
                tier = above(c.current_tier) if s.tier == "next" else s.tier
                if tier is not None and rank(tier) > rank(c.current_tier):
                    return Step(at, "escalate", tier, s.stage, "resolve")
                if c.severity == "critical" and c.current_tier == top:
                    return Step(at, "overdue", top, s.stage, "resolve")
                return Step(at, "skip", c.current_tier, s.stage, "resolve")

    if c.severity == "critical" and c.current_tier == top and c.top_tier_at is not None:
        return Step(c.top_tier_at + grace, "overdue", top, 0, "top")
    return None


def advance(c: Clock, step: Step, now: datetime, policy: Policy) -> Clock:
    """The clock after a step has been applied at time `now`."""
    changes: dict = {}
    if step.clock == "acknowledge":
        changes["ack_stage"] = step.stage
    elif step.clock == "resolve":
        changes["resolve_stage"] = step.stage
    if step.kind == "notify" and c.state == "open":
        changes["state"] = "notified"
    elif step.kind == "escalate":
        changes.update(current_tier=step.tier, state="escalated")
        if step.tier == policy.top_tier(c.severity, c.mode):
            changes["top_tier_at"] = now
    elif step.kind in ("overdue", "reminder"):
        changes.update(overdue=True, last_reminder_at=now)
    return replace(c, **changes)
