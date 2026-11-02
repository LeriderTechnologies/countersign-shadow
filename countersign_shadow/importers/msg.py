# SPDX-License-Identifier: Apache-2.0
"""Classic Outlook .msg files (an OLE compound file with MAPI properties), read with the vendored olefile (BSD-2-Clause).

extract-msg was not used because it is GPL-licensed; olefile is the permissive, pure-Python OLE reader it builds on.
Only the properties this tool needs are read: subject, sender, recipients, date, plain/HTML body, attachments.
If olefile is missing the file is skipped with a Dutch "sla op als .eml" message.
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import Parser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from .._vendor import load

NO_MSG_NL = ("Outlook .msg-bestanden lezen lukt niet: de meegeleverde olefile in countersign_shadow/_vendor ontbreekt. "
             "Open de mail in Outlook en kies "
             "Bestand > Opslaan als > .eml (of download hem via Outlook op het web).")

PT_UNICODE, PT_STRING8, PT_BINARY = "001F", "001E", "0102"
PR_SUBJECT, PR_BODY, PR_HTML = "0037", "1000", "1013"
PR_SENDER_NAME, PR_SENDER_EMAIL, PR_SENDER_ADDRESS = "0C1A", "0C1F", "5D01"
PR_DISPLAY_TO, PR_HEADERS, PR_MESSAGE_ID = "0E04", "007D", "1035"
PR_ATTACH_DATA, PR_ATTACH_LONG_NAME, PR_ATTACH_NAME, PR_ATTACH_MIME = "3701", "3707", "3704", "370E"
DATE_TAGS = (0x0039, 0x0E06)  # PR_CLIENT_SUBMIT_TIME, PR_MESSAGE_DELIVERY_TIME (PT_SYSTIME in __properties)


class MsgUnsupported(Exception):
    """Raised when a .msg cannot be read; the message is plain Dutch for the pilot user."""


def _olefile():
    try:
        olefile = load("olefile")
    except ImportError as exc:
        raise MsgUnsupported(NO_MSG_NL) from exc
    return olefile


def _prop(ole, prefix: list[str], tag: str, types=(PT_UNICODE, PT_STRING8)) -> str | bytes | None:
    for t in types:
        path = prefix + [f"__substg1.0_{tag}{t}"]
        if ole.exists("/".join(path)):
            raw = ole.openstream(path).read()
            if t == PT_UNICODE:
                return raw.decode("utf-16-le", "replace").rstrip("\x00")
            if t == PT_STRING8:
                return raw.decode("cp1252", "replace").rstrip("\x00")
            return raw
    return None


def _date(ole) -> str:
    if not ole.exists("__properties_version1.0"):
        return ""
    raw = ole.openstream("__properties_version1.0").read()
    found = {}
    for off in range(32, len(raw) - 15, 16):  # top-level message: 32-byte header, then 16-byte entries
        tag, _flags, value = struct.unpack_from("<IIQ", raw, off)
        if tag & 0xFFFF == 0x0040:  # PT_SYSTIME
            found[tag >> 16] = value
    for t in DATE_TAGS:
        if found.get(t):
            try:
                dt = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=found[t] // 10)
            except OverflowError:  # corrupt timestamp: fall back to the Date: header instead of failing the mail
                continue
            return dt.date().isoformat()
    return ""


def _header_date(headers) -> str:
    try:
        return parsedate_to_datetime(str(headers["Date"])).date().isoformat() if headers.get("Date") else ""
    except (TypeError, ValueError):
        return ""


def read_msg_parts(path: Path) -> dict:
    """The fields of one .msg as plain values; attachments as (name, content_type, bytes)."""
    olefile = _olefile()
    if not olefile.isOleFile(str(path)):
        raise MsgUnsupported("Dit .msg-bestand is geen geldig Outlook-bericht. Sla de mail op als .eml.")
    try:
        ole = olefile.OleFileIO(str(path))
    except Exception as exc:  # malformed compound file
        raise MsgUnsupported("Dit .msg-bestand kon niet gelezen worden. Sla de mail op als .eml.") from exc
    try:
        return _read_parts(ole)
    except MsgUnsupported:
        raise
    except Exception as exc:  # noqa: BLE001 -- a damaged OLE tree makes olefile raise OSError, ValueError,
        # OverflowError, RecursionError, ...; the whole mail is reported (never half-read, so an attachment
        # with an IBAN can never vanish silently)
        raise MsgUnsupported("Dit .msg-bestand is beschadigd en kon niet volledig gelezen worden "
                             f"({type(exc).__name__}). Sla de mail op als .eml.") from exc
    finally:
        ole.close()


def _read_parts(ole) -> dict:
    """The message fields from an opened OLE file; any olefile error propagates to read_msg_parts."""
    headers = Parser(policy=policy.default).parsestr(_prop(ole, [], PR_HEADERS) or "", headersonly=True)
    subject = _prop(ole, [], PR_SUBJECT) or str(headers.get("Subject") or "")
    name, addr = parseaddr(str(headers.get("From") or ""))
    name = _prop(ole, [], PR_SENDER_NAME) or name
    for cand in (_prop(ole, [], PR_SENDER_ADDRESS), _prop(ole, [], PR_SENDER_EMAIL), addr):
        if cand and "@" in cand:
            addr = cand
            break
    body = _prop(ole, [], PR_BODY) or ""
    html_raw = _prop(ole, [], PR_HTML, (PT_BINARY, PT_UNICODE, PT_STRING8))
    html = html_raw.decode("utf-8", "replace") if isinstance(html_raw, bytes) else (html_raw or "")
    attachments = []
    for d in sorted({e[0] for e in ole.listdir(streams=False, storages=True)
                     if e and e[0].startswith("__attach_version1.0_")}):
        data = _prop(ole, [d], PR_ATTACH_DATA, (PT_BINARY,))
        fname = _prop(ole, [d], PR_ATTACH_LONG_NAME) or _prop(ole, [d], PR_ATTACH_NAME) or "(unnamed)"
        ctype = _prop(ole, [d], PR_ATTACH_MIME) or ""
        attachments.append((fname, ctype, data if isinstance(data, bytes) else b""))
    return {"subject": subject.strip(), "from_name": name or "", "from": (addr or "").lower(),
            "to": _prop(ole, [], PR_DISPLAY_TO) or str(headers.get("To") or ""),
            "message_id": _prop(ole, [], PR_MESSAGE_ID) or str(headers.get("Message-ID") or ""),
            "date": _date(ole) or _header_date(headers), "body": body, "html": html, "attachments": attachments}
