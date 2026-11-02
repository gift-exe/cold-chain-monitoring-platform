"""Device message format and validation (FR1.1-FR1.3, FR1.5, FR2.3, FR2.4).

A device publishes to the MQTT topic ``devices/<serial>/readings`` either one reading:

    {"device_id": "TEST-FIX-0001", "seq_no": 42, "event_time": "2026-11-01T10:00:00Z", "temp": 5.1,
     "power_ok": true, "door_open": false, "lat": 11.98, "lon": 8.49, "battery": 87}

or a batch of buffered readings uploaded after a period offline (FR1.2):

    {"device_id": "TEST-MOB-0001", "readings": [{"seq_no": 1, "event_time": "...", "temp": 4.8}, ...]}

Each reading in a batch is validated separately, so one bad reading does not cause the rest to be lost.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_FUTURE = timedelta(minutes=5)        # FR2.4
LATE_HORIZON = timedelta(hours=72)       # FR2.3
MAX_BATCH = 5000                         # 72 h at one reading per minute is 4,320


class ReadingIn(BaseModel):
    """One reading as sent by a device."""
    model_config = ConfigDict(extra="forbid")

    seq_no: int = Field(ge=0)
    event_time: AwareDatetime
    temp: float = Field(ge=-50, le=70)   # assumed physical range of the sensor; outside it is a fault
    power_ok: bool | None = None
    door_open: bool | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    battery: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _position_complete(self):
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        return self


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)
    readings: list[dict] = Field(min_length=1, max_length=MAX_BATCH)


@dataclass(frozen=True)
class Accepted:
    serial: str
    reading: ReadingIn
    received_at: datetime
    late: bool

    def to_record(self, device_uuid: str) -> dict:
        """The form published to the event log and stored in the database."""
        r = self.reading
        return {"device_id": device_uuid, "serial": self.serial, "seq_no": r.seq_no,
                "event_time": r.event_time.isoformat(), "received_at": self.received_at.isoformat(),
                "temperature": r.temp, "power_ok": r.power_ok, "door_open": r.door_open,
                "lat": r.lat, "lon": r.lon, "battery": r.battery, "late_flag": self.late}


@dataclass(frozen=True)
class Rejected:
    reason: str
    payload: str
    serial: str | None = None


def _short(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'message'}: {err['msg']}" for err in e.errors())


def parse(topic: str, payload: bytes, received_at: datetime) -> tuple[list[Accepted], list[Rejected]]:
    """Validate one MQTT message. Returns the readings accepted and the reasons for any rejections."""
    text = payload.decode("utf-8", errors="replace")
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "devices" or parts[2] != "readings":
        return [], [Rejected(f"unexpected topic {topic}", text)]
    topic_serial = parts[1]

    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        return [], [Rejected(f"invalid JSON: {e.msg}", text, topic_serial)]
    if not isinstance(doc, dict):
        return [], [Rejected("message is not a JSON object", text, topic_serial)]

    if "readings" not in doc:  # single reading: same fields as a batch item plus device_id
        doc = {"device_id": doc.pop("device_id", None), "readings": [doc]}
    try:
        batch = BatchIn.model_validate(doc)
    except ValidationError as e:
        return [], [Rejected(_short(e), text, topic_serial)]
    if batch.device_id != topic_serial:
        # A device may only publish its own readings
        return [], [Rejected(f"device_id {batch.device_id!r} does not match topic", text, topic_serial)]

    accepted, rejected = [], []
    for item in batch.readings:
        try:
            r = ReadingIn.model_validate(item)
        except ValidationError as e:
            rejected.append(Rejected(_short(e), json.dumps(item), batch.device_id))
            continue
        if r.event_time > received_at + MAX_FUTURE:
            rejected.append(Rejected("event_time more than 5 minutes in the future", json.dumps(item),
                                     batch.device_id))
            continue
        late = received_at - r.event_time > LATE_HORIZON
        accepted.append(Accepted(batch.device_id, r, received_at, late))
    return accepted, rejected
