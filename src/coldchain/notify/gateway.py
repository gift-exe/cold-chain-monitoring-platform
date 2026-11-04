"""The SMS gateway client: Africa's Talking's bulk messaging API (POST /version1/messaging, form-encoded,
authenticated with the apiKey header). The same client talks to the live service, the sandbox or the
local emulator; only the base URL and key differ.

The send response gives a status per recipient. Later, the gateway posts delivery reports to the
notification service's callback; those are interpreted by `outcome()` below.
"""
from dataclasses import dataclass

import httpx

from coldchain.config import get_settings

# Per-recipient status codes in the send response (Africa's Talking)
ACCEPTED = {100, 101, 102}                 # Processed, Sent, Queued
PERMANENT = {403, 404, 406}                # InvalidPhoneNumber, UnsupportedNumberType, UserInBlacklist
# anything else (401 RiskHold, 405 InsufficientBalance, 407 CouldNotRoute, 5xx gateway errors) is retried

# Delivery report statuses
FINAL_SUCCESS = {"Success"}
FINAL_REJECTED = {"Rejected"}
FINAL_FAILED = {"Failed", "AbsentSubscriber", "Expired"}
# failure reasons that no retry can cure
PERMANENT_REASONS = {"UserInBlackList", "UserDoesNotExist", "UserAccountSuspended", "NotNetworkSubscriber",
                     "InvalidPhoneNumber"}


@dataclass(frozen=True)
class SendResult:
    outcome: str                 # sent | rejected | failed (temporary)
    message_id: str | None
    status: str                  # the gateway's words, or the error
    cost: str | None = None


def send(phone: str, message: str, client: httpx.Client | None = None) -> SendResult:
    s = get_settings()
    data = {"username": s.at_username, "to": s.sms_redirect_to or phone, "message": message, "bulkSMSMode": "1"}
    if s.at_sender_id:
        data["from"] = s.at_sender_id
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.post(f"{s.at_api_url}/version1/messaging", data=data,
                        headers={"apiKey": s.at_api_key, "Accept": "application/json"})
        if r.status_code >= 500:
            return SendResult("failed", None, f"HTTP {r.status_code}")
        if r.status_code >= 400:   # bad key or request: a fault on our side, retried while it is fixed
            return SendResult("failed", None, f"HTTP {r.status_code}: {r.text[:200]}")
        recipients = r.json()["SMSMessageData"]["Recipients"]
        if not recipients:
            return SendResult("rejected", None, r.json()["SMSMessageData"].get("Message", "no recipients"))
        rec = recipients[0]
        code = int(rec["statusCode"])
        outcome = "sent" if code in ACCEPTED else "rejected" if code in PERMANENT else "failed"
        return SendResult(outcome, rec.get("messageId") if outcome == "sent" else None, rec["status"],
                          rec.get("cost"))
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return SendResult("failed", None, f"{type(e).__name__}: {e}")
    finally:
        if own:
            client.close()


def outcome(status: str, failure_reason: str | None) -> str | None:
    """Interpret a delivery report: delivered, rejected or failed (temporary), or None while in progress
    (Sent, Submitted, Buffered)."""
    if status in FINAL_SUCCESS:
        return "delivered"
    if status in FINAL_REJECTED or failure_reason in PERMANENT_REASONS:
        return "rejected"
    if status in FINAL_FAILED:
        return "failed"
    return None
