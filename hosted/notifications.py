"""Idempotent email alerts for official, locked paper decisions only."""
from __future__ import annotations

import hashlib
import json
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests

from tracking import american_odds


def recipient_hash(address: str) -> str:
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


def qualifying_decisions(ledger):
    output = []
    for row in ledger.rows("""SELECT d.*,g.home,g.away FROM decisions d JOIN games g ON g.id=d.game_id
            ORDER BY d.created,d.policy_id,d.game_id"""):
        payload = json.loads(row["payload"])
        bet = payload.get("bets", {}).get("model_value", {})
        side = bet.get("selection")
        if payload.get("reason") or side not in ("home", "away"):
            continue
        team = row[side]
        probability = payload["probability"] if side == "home" else 1 - payload["probability"]
        odds = bet["odds"]
        output.append(dict(
            event_key=f"locked:{row['policy_id']}:{row['game_id']}", game_id=row["game_id"],
            home=row["home"], away=row["away"], team=team, cutoff=row["cutoff"],
            tipoff=payload["tipoff"], probability=probability, decimal_odds=odds,
            american_odds=american_odds(odds), ev=probability * odds - 1,
        ))
    return output


class EmailNotifier:
    def __init__(self, recipient: str, provider: str = "smtp"):
        self.recipient = recipient.strip()
        self.provider = provider

    @classmethod
    def from_env(cls):
        address = os.environ.get("ALERT_EMAIL", "").strip()
        return cls(address, os.environ.get("ALERT_PROVIDER", "smtp").lower()) if address else None

    def send(self, alert: dict, event_key: str) -> str:
        subject = f"Courtside locked value: {alert['team']}"
        signed = f"+{alert['american_odds']}" if alert["american_odds"] > 0 else str(alert["american_odds"])
        site = os.environ.get("COURTSIDE_URL", "https://courtside-nba.pages.dev")
        text = (f"Official paper decision: {alert['away']} at {alert['home']}\n"
                f"Selection: {alert['team']} ({alert['probability']:.1%})\n"
                f"Locked price: {signed} / {alert['decimal_odds']:.2f}\n"
                f"Estimated EV: {alert['ev']:.1%}\nTipoff: {alert['tipoff']}\n"
                f"Research estimate only; no wager was placed.\n{site}/#games/game/{alert['game_id']}")
        if self.provider == "resend":
            return self._resend(subject, text, event_key)
        if self.provider == "smtp":
            return self._smtp(subject, text)
        raise RuntimeError("Unsupported alert provider.")

    def _resend(self, subject, text, event_key):
        response = requests.post("https://api.resend.com/emails", timeout=20,
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                     "Idempotency-Key": event_key},
            json={"from": os.environ["ALERT_FROM"], "to": [self.recipient],
                  "subject": subject, "text": text})
        if response.status_code >= 300:
            raise RuntimeError(f"Email provider returned HTTP {response.status_code}.")
        return str(response.json().get("id", "resend-accepted"))

    def _smtp(self, subject, text):
        message = EmailMessage()
        message["Subject"], message["From"], message["To"] = subject, os.environ["SMTP_FROM"], self.recipient
        message.set_content(text)
        host, port = os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "465"))
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as client:
            client.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            client.send_message(message)
        return "smtp-accepted"


def deliver_locked_alerts(ledger, notifier, now=None):
    if notifier is None:
        return {"sent": 0, "failed": 0, "disabled": True}
    sent = failed = 0
    hashed = recipient_hash(notifier.recipient)
    for alert in qualifying_decisions(ledger):
        delivery_id = ledger.reserve_notification(alert["event_key"], "email", hashed, now)
        if delivery_id is None:
            continue
        try:
            provider_id = notifier.send(alert, alert["event_key"])
            ledger.finish_notification(delivery_id, "sent", provider_id, sent=now)
            sent += 1
        except Exception as exc:
            ledger.finish_notification(delivery_id, "failed", error=type(exc).__name__, sent=now)
            failed += 1
    return {"sent": sent, "failed": failed, "disabled": False}
