"""SMS gateway emulator: a stand-in for Africa's Talking that speaks the same protocol (Section 3.4.6).

The Africa's Talking sandbox shows that messages are composed, sent and parsed correctly, but it cannot be
told to fail, and its callbacks need an address reachable from the internet. This emulator can, so the
failure handling in Table 3.20 can be tested on demand and repeatably:
- POST /version1/messaging accepts messages exactly as Africa's Talking does (form fields username, to,
  message; header apiKey) and answers in its format;
- after a delay, it posts a delivery report (id, status, phoneNumber, networkCode, failureReason,
  retryCount) to the notification service, as the gateway would;
- POST /_sim/inbound posts an incoming message (date, from, id, linkId, text, to, networkCode) as if a
  phone had sent it;
- PUT /_sim/numbers/{phone} sets how messages to a number fare: success (default), failed (temporary),
  rejected, invalid (refused when sent), or silent (no delivery report); `times` limits a failure to the
  first n messages, after which they succeed;
- GET /_sim/messages lists what was sent (the emulator's message log).

Run: uv run uvicorn coldchain.simulator.sms_gateway:app --port 8002
"""
import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import FastAPI, Form, Header, HTTPException
from pydantic import BaseModel

log = logging.getLogger("sms-gateway")
API_KEY = os.environ.get("AT_API_KEY", "dev-at-key")
DELIVERY_URL = os.environ.get("SMS_DELIVERY_CALLBACK", "http://localhost:8001/sms/dev-callback-token/delivery")
INBOUND_URL = os.environ.get("SMS_INBOUND_CALLBACK", "http://localhost:8001/sms/dev-callback-token/inbound")
SHORTCODE = os.environ.get("SMS_SHORTCODE", "40404")
NETWORK = "62130"             # MTN Nigeria (MCC 621, MNC 30)
FAILURE_REASONS = {"failed": "DeliveryFailure", "rejected": "UserInBlackList"}

app = FastAPI(title="SMS gateway emulator (Africa's Talking protocol)")
messages: list[dict] = []
behaviour: dict[str, dict] = {}


class Behaviour(BaseModel):
    outcome: str = "success"          # success | failed | rejected | invalid | silent
    times: int | None = None          # only the first n messages; None: all
    delay_s: float = 0.2              # before the delivery report


class Inbound(BaseModel):
    sender: str
    text: str
    id: str | None = None             # give the same id twice to simulate a duplicate delivery


def _fate(phone: str) -> Behaviour:
    b = behaviour.get(phone)
    if b is None:
        return Behaviour()
    rule = b["rule"]
    if rule.times is not None:
        if b["used"] >= rule.times:
            return Behaviour(delay_s=rule.delay_s)
        b["used"] += 1
    return rule


async def _report(message: dict, fate: Behaviour) -> None:
    await asyncio.sleep(fate.delay_s)
    if fate.outcome == "silent":
        return
    status = {"success": "Success", "failed": "Failed", "rejected": "Rejected"}[fate.outcome]
    form = {"id": message["messageId"], "status": status, "phoneNumber": message["to"], "networkCode": NETWORK,
            "retryCount": "0"}
    if fate.outcome in FAILURE_REASONS:
        form["failureReason"] = FAILURE_REASONS[fate.outcome]
    message["status"] = status
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(DELIVERY_URL, data=form)
            message["report_http"] = r.status_code
        except httpx.HTTPError as e:
            log.warning("delivery report for %s not accepted: %s", message["messageId"], e)


@app.post("/version1/messaging", status_code=201)
async def send(username: Annotated[str, Form()], to: Annotated[str, Form()], message: Annotated[str, Form()],
               apiKey: Annotated[str | None, Header()] = None):
    if apiKey != API_KEY:
        raise HTTPException(401, "The supplied authentication is invalid")
    recipients = []
    for phone in [p.strip() for p in to.split(",") if p.strip()]:
        fate = _fate(phone)
        record = {"to": phone, "message": message, "username": username, "at": datetime.now(UTC).isoformat(),
                  "fate": fate.outcome, "status": "Sent"}
        if fate.outcome == "invalid":
            recipients.append({"statusCode": 403, "number": phone, "status": "InvalidPhoneNumber", "cost": "0",
                               "messageId": "None"})
            record.update(messageId=None, status="InvalidPhoneNumber")
        else:
            record["messageId"] = f"ATXid_{uuid.uuid4().hex}"
            recipients.append({"statusCode": 101, "number": phone, "status": "Success", "cost": "NGN 2.2000",
                               "messageId": record["messageId"]})
            asyncio.get_running_loop().create_task(_report(record, fate))
        messages.append(record)
        log.info("SMS to %s (%d chars, %s): %s", phone, len(message), fate.outcome, message)
    sent = sum(r["statusCode"] == 101 for r in recipients)
    return {"SMSMessageData": {"Message": f"Sent to {sent}/{len(recipients)} Total Cost: NGN {2.2 * sent:.4f}",
                               "Recipients": recipients}}


@app.post("/_sim/inbound")
async def inbound(body: Inbound):
    gateway_id = body.id or str(uuid.uuid4())
    form = {"date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), "from": body.sender, "id": gateway_id,
            "linkId": uuid.uuid4().hex, "text": body.text, "to": SHORTCODE, "networkCode": NETWORK}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(INBOUND_URL, data=form)
    return {"id": gateway_id, "callback_status": r.status_code}


@app.put("/_sim/numbers/{phone}")
async def set_behaviour(phone: str, body: Behaviour):
    behaviour[phone] = {"rule": body, "used": 0}
    return body


@app.delete("/_sim/numbers")
async def reset_behaviour():
    behaviour.clear()


@app.get("/_sim/messages")
async def sent_messages(to: str | None = None, since: str | None = None):
    return [m for m in messages if (to is None or m["to"] == to) and (since is None or m["at"] >= since)]


@app.get("/health")
async def health():
    return {"status": "ok"}
