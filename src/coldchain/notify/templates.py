"""SMS templates (Section 3.4.6). Every message fits in one SMS of 160 characters (NFR9).

160 characters is the limit only for text in the GSM 7-bit alphabet. A single character outside it (a
Yoruba or Hausa name with a dot below or a hooked letter, a degree sign) makes the whole message UCS-2,
whose limit is 70, so names and places are transliterated first. Variable parts that can be long (place
and person names) are shortened until the message fits; numbers, codes and commands never are.
"""
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

LIMIT = 160
# GSM 03.38 basic character set (characters of the extension table, such as [ ] { } and the euro sign,
# take two places and are not used)
GSM_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")

TYPES = {"heat_excursion": "HEAT", "freeze": "FREEZE", "power_loss": "POWER OFF", "door_open": "DOOR OPEN",
         "device_silence": "NO DATA", "custody_gap": "CUSTODY GAP", "anomaly": "UNUSUAL"}
EQUIPMENT = {"ice_lined_refrigerator": "ILR", "solar_direct_drive": "SDD fridge", "vaccine_carrier": "carrier",
             "cold_box": "cold box", "freezer": "freezer", "walk_in_cold_room": "cold room"}

HELP = ("Commands: ACK <incident>; RES <incident> <code> <note>; REC <shipment> <code>; "
        "REJ <shipment> <code> <reason>. Closing is on the dashboard.")


class TooLong(ValueError):
    pass


def gsm(text: str) -> str:
    """Transliterate to the GSM 7-bit alphabet: accents and dots below are removed; anything else that
    cannot be sent is replaced by '?'."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c if c in GSM_BASIC else "?" for c in plain)


def fits(text: str) -> bool:
    return len(text) <= LIMIT and all(c in GSM_BASIC for c in text)


def fit(render, shrinkable: dict[str, str]) -> str:
    """Render a template; if it is too long, shorten the longest shrinkable field, never below the next
    longest, until it fits (so a long place and a long name are shortened alike)."""
    parts = {k: gsm(v) for k, v in shrinkable.items()}
    while True:
        text = render(**parts)
        excess = len(text) - LIMIT
        if excess <= 0:
            return text
        ranked = sorted(parts, key=lambda k: len(parts[k]), reverse=True)
        longest = parts[ranked[0]]
        if len(longest) <= 3:
            raise TooLong(text)
        floor = len(parts[ranked[1]]) if len(ranked) > 1 else 0
        cut = min(excess, max(1, len(longest) - floor))
        parts[ranked[0]] = longest[:max(3, len(longest) - cut)].rstrip(" ,-")


def duration(d: timedelta) -> str:
    minutes = max(0, round(d.total_seconds() / 60))
    if minutes < 120:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // 1440} d"


@dataclass(frozen=True)
class Incident:
    """What a message says about an incident."""
    no: int
    type: str
    severity: str
    place: str                    # facility and equipment, or the consignment
    peak: float | None
    started: datetime
    ended: datetime | None
    owner: str                    # owner's name
    acknowledged: bool

    def label(self) -> str:
        return f"{'CRIT' if self.severity == 'critical' else 'WARN'} {TYPES.get(self.type, self.type.upper())}"

    def reading(self, now: datetime) -> str:
        lasted = duration((self.ended or now) - self.started)
        if self.type in ("heat_excursion", "freeze") and self.peak is not None:
            return f"{self.peak:.1f}C for {lasted}"
        if self.type == "custody_gap":
            return "handoff not confirmed"
        if self.type == "anomaly":
            return "unusual readings"
        return f"for {lasted}"


def place(facility: str | None, equipment: str | None, consignment_no: int | None, destination: str | None) -> str:
    if facility:
        eq = EQUIPMENT.get(equipment or "", (equipment or "").replace("_", " "))
        return f"{facility} {eq}".strip()
    if consignment_no is not None:
        return f"shipment {consignment_no}" + (f" to {destination}" if destination else "")
    return "unknown location"


def name(full_name: str) -> str:
    """'Amina Bello Yusuf' -> 'A. Yusuf'."""
    words = full_name.split()
    return full_name if len(words) < 2 else f"{words[0][0]}. {words[-1]}"


# Incident messages ----------------------------------------------------------------------------------------

def alert(i: Incident, code: str, now: datetime) -> str:
    return fit(lambda place: f"{i.label()} {place}: {i.reading(now)}. You own #{i.no}. Reply ACK {i.no}. "
                             f"When fixed: RES {i.no} {code} note", {"place": i.place})


def cc(i: Incident, now: datetime) -> str:
    return fit(lambda place, owner: f"FYI {i.label()} #{i.no} {place}: {i.reading(now)}. Owner {owner}. "
                                    "You are copied; no reply needed.", {"place": i.place, "owner": i.owner})


def escalation(i: Incident, code: str, now: datetime, undelivered: bool = False) -> str:
    if undelivered:
        why = "alert could not be delivered to {owner}"
    else:
        since = duration(now - i.started)
        why = ("{owner} has not resolved it" if i.acknowledged else "{owner} has not acknowledged") + f" ({since})"
    return fit(lambda place, owner: f"ESC {i.label()} #{i.no} {place}: {i.reading(now)}. "
                                    + why.format(owner=owner)
                                    + f". Reply ACK {i.no}; when fixed RES {i.no} {code} note",
               {"place": i.place, "owner": i.owner})


def overdue(i: Incident, code: str, now: datetime, reminder: bool = False) -> str:
    head = "REMINDER OVERDUE" if reminder else "OVERDUE"
    return fit(lambda place, owner: f"{head} {i.label()} #{i.no} {place}, open {duration(now - i.started)}. "
                                    f"Owner {owner}. When fixed: RES {i.no} {code} note",
               {"place": i.place, "owner": i.owner})


def reopened(i: Incident, code: str, now: datetime) -> str:
    return fit(lambda place: f"REOPENED {i.label()} #{i.no} {place}: reviewer judged the action inadequate. "
                             f"You remain owner. When fixed: RES {i.no} {code} note", {"place": i.place})


def digest(day: datetime, numbers: list[int]) -> str:
    """Retrospective incidents: one daily, non-urgent message listing incident numbers."""
    head = f"RETRO {day:%d %b}: {len(numbers)} past incident{'s' if len(numbers) != 1 else ''} to review " \
           "on the dashboard:"
    listed = []
    for i, no in enumerate(numbers):
        more = len(numbers) - i - 1
        candidate = " ".join([head, *listed, f"#{no}"]) + (f" +{more} more" if more else "")
        if len(candidate) > LIMIT:
            return " ".join([head, *listed]) + f" +{len(numbers) - i} more"
        listed.append(f"#{no}")
    return " ".join([head, *listed])


# Custody and administration -------------------------------------------------------------------------------

def handoff(consignment_no: int, releaser: str, quantity: int, product: str, code: str) -> str:
    return fit(lambda releaser, product: f"Shipment {consignment_no} from {releaser}: {quantity} {product}. "
                                         f"On arrival reply REC {consignment_no} {code}. If unfit: "
                                         f"REJ {consignment_no} {code} reason",
               {"releaser": releaser, "product": product})


def handoff_undelivered(consignment_no: int, receiver: str) -> str:
    return fit(lambda receiver: f"Could not reach {receiver} by SMS about consignment {consignment_no}. "
                                "Ask them to confirm receipt on the dashboard.", {"receiver": receiver})


def admin_rejected(user: str, phone: str | None, reason: str) -> str:
    return fit(lambda user, reason: f"ADMIN: SMS to {user} ({phone or 'no number'}) was rejected: {reason}. "
                                    "Please correct the number.", {"user": user, "reason": reason})


def dashboard(kind: str, i: Incident | None, now: datetime, consignment_no: int | None = None) -> str:
    """The line shown in the dashboard's notification list (no reply instructions, no code)."""
    if i is None:
        return f"Consignment {consignment_no}: handoff to you awaiting confirmation" if kind == "handoff" \
            else kind
    heading = {"alert": "New incident", "cc": "Copied", "escalation": "Escalated to you", "overdue": "Overdue",
               "reminder": "Overdue reminder", "reopened": "Reopened", "undelivered": "SMS not delivered",
               "review": "Resolved, awaiting your review"}
    return f"{heading.get(kind, kind)}: {i.label()} #{i.no} {gsm(i.place)}: {i.reading(now)}"
