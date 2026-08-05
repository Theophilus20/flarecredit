"""FlareCredit  support contact endpoint (Resend).

Sends two emails per submission:
  1. Notification to the team, with reply-to set to the sender
  2. Branded auto-reply confirming receipt

Setup
-----
  pip install "pydantic[email]"

  .env:
    RESEND_API_KEY=re_xxxxxxxxxxxx
    SUPPORT_FROM=FlareCredit <support@yourdomain.com>   # domain verified in Resend
    SUPPORT_TO=you@yourdomain.com

Wire up in app/main.py:
    from .fc_support import router as fc_support_router
    app.include_router(fc_support_router)
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

router = APIRouter(prefix="/api/support", tags=["support"])

RESEND_URL = "https://api.resend.com/emails"
PINK = "#E62058"
INK = "#15121C"
MUTE = "#635C72"

_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(key: str, limit: int = 3, window_s: int = 900) -> None:
    q = _hits[key]
    now = time.monotonic()
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many messages  please try again later, or email us directly.")
    q.append(now)


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    topic: str = Field(default="General question", max_length=80)
    message: str = Field(min_length=5, max_length=4000)
    website: str = Field(default="", max_length=200)  # honeypot


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _team_email(d: ContactIn) -> str:
    return f"""<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:620px;color:{INK}">
<div style="border-left:3px solid {PINK};padding-left:14px;margin-bottom:22px">
  <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{MUTE}">FlareCredit support</div>
  <div style="font-size:20px;font-weight:700">New message · {_esc(d.topic)}</div>
</div>
<table style="border-collapse:collapse;width:100%;font-size:14px;margin-bottom:20px">
  <tr><td style="padding:7px 0;color:{MUTE};width:80px">From</td><td><b>{_esc(d.name)}</b></td></tr>
  <tr><td style="padding:7px 0;color:{MUTE}">Email</td><td><a href="mailto:{d.email}" style="color:{PINK}">{d.email}</a></td></tr>
  <tr><td style="padding:7px 0;color:{MUTE}">Topic</td><td>{_esc(d.topic)}</td></tr>
</table>
<div style="padding:18px;background:#F7F5FA;border-radius:14px;white-space:pre-wrap;
            font-size:14.5px;line-height:1.65">{_esc(d.message)}</div>
<p style="color:{MUTE};font-size:12px;margin-top:18px">Reply to this email to respond directly to {_esc(d.name)}.</p>
</div>"""


def _auto_reply(name: str, topic: str) -> str:
    return f"""<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:560px;color:{INK}">
<div style="font-family:Georgia,serif;font-size:23px;font-weight:700;letter-spacing:-.5px;margin-bottom:26px">
  Flare<span style="color:{PINK}">Credit</span></div>
<p style="font-size:15.5px;line-height:1.7">Hi {_esc(name)},</p>
<p style="font-size:15.5px;line-height:1.7">
  Thanks for getting in touch your message about <b>{_esc(topic)}</b> has reached us, and a
  member of the team will reply personally, usually within one business day.</p>
<p style="font-size:15.5px;line-height:1.7">
  If it's urgent, or you'd like a faster answer, our
  <a href="https://flarecredit.xyz/docs" style="color:{PINK}">documentation</a> covers setup,
  attestation timing, scoring rules, and troubleshooting in detail.</p>
<p style="font-size:15.5px;line-height:1.7"> The FlareCredit team</p>
<div style="border-top:1px solid #E8E3EF;margin:28px 0 0;padding-top:18px">
  <p style="color:{MUTE};font-size:12px;line-height:1.7;margin:0">
    This is an automated confirmation you can reply to it and we'll see your response.<br>
    FlareCredit · private credit scores for XRP holders, built on Flare.<br>
    © 2026 FlareCredit. All rights reserved.</p>
</div></div>"""


async def _send(api_key: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            RESEND_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    if r.status_code >= 300:
        raise HTTPException(502, f"Email service returned {r.status_code}. Please email us directly.")


@router.post("/contact")
async def contact(body: ContactIn):
    if body.website:                       # bot tripped the honeypot
        return {"ok": True}

    api_key = os.getenv("RESEND_API_KEY", "")
    sender = os.getenv("SUPPORT_FROM", "FlareCredit <onboarding@resend.dev>")
    to = os.getenv("SUPPORT_TO", "")
    if not api_key or not to:
        raise HTTPException(503, "Support email isn't configured yet  please email us directly.")

    _rate_limit(body.email.lower())

    await _send(api_key, {
        "from": sender, "to": [to], "reply_to": body.email,
        "subject": f"[FlareCredit] {body.topic}  {body.name}",
        "html": _team_email(body),
    })
    await _send(api_key, {
        "from": sender, "to": [body.email],
        "subject": "We've received your message  FlareCredit",
        "html": _auto_reply(body.name, body.topic),
    })
    return {"ok": True}
