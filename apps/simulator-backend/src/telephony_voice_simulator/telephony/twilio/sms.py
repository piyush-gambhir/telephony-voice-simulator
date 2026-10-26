"""Signed, allowlisted SMS inbox for controlled test numbers."""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web


def allowed_numbers() -> set[str]:
    return set(filter(None, os.environ.get("PSTN_SMS_NUMBERS", "").split(",")))


def allowed_senders() -> set[str]:
    return set(filter(None, os.environ.get("PSTN_SMS_ALLOWED_SENDERS", "").split(",")))


def list_messages(limit: int = 50) -> list[dict[str, str]]:
    """Newest first. Empty when the inbox is unconfigured or nothing has arrived yet."""
    path = os.environ.get("PSTN_SMS_DB", "")
    if not path or not Path(path).is_file():
        return []
    # Read-only so inspecting the inbox can never create or alter it.
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT message_sid, sender, recipient, body, received_at FROM sms_messages "
                "ORDER BY received_at DESC, rowid DESC LIMIT ?", (limit,),
            ).fetchall()
    except sqlite3.OperationalError:
        # The file exists but the table does not until the first delivery.
        return []
    return [dict(row) for row in rows]


async def handle_sms(request: web.Request) -> web.Response:
    from . import security, twiml

    form = await request.post()
    if rejected := security.reject_invalid_request(request, form):
        return rejected
    allowed = allowed_numbers()
    callers = allowed_senders()
    path = os.environ.get("PSTN_SMS_DB", "")
    if (not path or str(form.get("To", "")) not in allowed
            or str(form.get("From", "")) not in callers
            or str(form.get("AccountSid", "")) != os.environ.get("TWILIO_ACCOUNT_SID")):
        raise web.HTTPForbidden()
    sid = str(form.get("MessageSid", ""))
    body = str(form.get("Body", ""))
    if not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid) or len(body) > 1600:
        raise web.HTTPBadRequest()
    if str(form.get("NumMedia", "0")) != "0":
        raise web.HTTPUnsupportedMediaType()
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    # A SQLite transaction context commits or rolls back, but does not close.
    with closing(sqlite3.connect(database)) as db, db:
        database.chmod(0o600)
        db.execute("""CREATE TABLE IF NOT EXISTS sms_messages (
            message_sid TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
            body TEXT NOT NULL, received_at TEXT NOT NULL)""")
        if not db.execute("SELECT 1 FROM sms_messages WHERE message_sid=?", (sid,)).fetchone():
            if db.execute("SELECT COUNT(*) FROM sms_messages").fetchone()[0] >= 1000:
                raise web.HTTPServiceUnavailable(reason="Test inbox full")
            db.execute("INSERT INTO sms_messages VALUES (?,?,?,?,?)", (
                sid, str(form["From"]), str(form["To"]), body,
                datetime.now(UTC).isoformat(),
            ))
    return twiml.twiml("")


def register_routes(app: web.Application) -> None:
    app.router.add_post("/twilio/sms", handle_sms)
