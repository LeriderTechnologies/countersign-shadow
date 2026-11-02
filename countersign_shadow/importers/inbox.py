# SPDX-License-Identifier: Apache-2.0
"""import-inbox: read .eml files, .mbox mailboxes and Outlook .msg files into plain message dicts.

Nothing is sent anywhere. PDF attachments are read for their text layer (optional pypdf, see attachments.py) so the
checks can compare the invoice's IBAN and total with the mail text and with your own payments. Other attachments
are not opened; only their file names are kept. Outlook .msg needs the optional olefile (see msg.py).
"""
from __future__ import annotations

import hashlib
import html
import mailbox
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from . import attachments as att
from . import msg as msglib

MAX_BODY = 8000  # characters kept per message (enough for any payment request; keeps the planner input small)


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", s)


def _body(msg: EmailMessage) -> tuple[str, list[str], list[dict]]:
    attachments: list[str] = []
    pdfs: list[dict] = []
    plain, htm = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        ctype = part.get_content_type()
        if disp == "attachment" or att.is_pdf(part.get_filename(), ctype):
            name = part.get_filename() or "(unnamed)"
            attachments.append(name)
            if att.is_pdf(name, ctype):
                pdfs.append(att.read_pdf_attachment(name, part.get_payload(decode=True) or b""))
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("latin-1", "replace")
        if not isinstance(content, str):
            continue
        if ctype == "text/plain" and plain is None:
            plain = content
        elif ctype == "text/html" and htm is None:
            htm = content
    return _clean(plain if plain is not None else _html_to_text(htm or "")), attachments, pdfs


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()[:MAX_BODY]


def _message_id(msgid: str, sender: str, subject: str, body: str) -> str:
    key = msgid or (sender + subject + body[:500])
    return "m-" + hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:10]


def parse_message(msg: EmailMessage, origin: str) -> dict:
    body, attachments, pdfs = _body(msg)
    name, addr = parseaddr(str(msg.get("From", "")))
    date = ""
    try:
        if msg.get("Date"):
            date = parsedate_to_datetime(str(msg["Date"])).date().isoformat()
    except (TypeError, ValueError):
        date = ""
    mid = _message_id(str(msg.get("Message-ID") or ""), str(msg.get("From")), str(msg.get("Subject")), body)
    return {"id": mid, "from": addr.lower(), "from_name": name, "to": str(msg.get("To", "")),
            "subject": str(msg.get("Subject", "")).strip(), "date": date, "body": body,
            "attachments": attachments, "pdfs": pdfs, "origin": origin}


def read_eml(path: Path) -> dict:
    with open(path, "rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)
    return parse_message(msg, Path(path).name)


def read_mbox(path: Path) -> list[dict]:
    out = []
    box = mailbox.mbox(str(path), factory=lambda f: BytesParser(policy=policy.default).parse(f), create=False)
    try:
        for i, msg in enumerate(box):
            out.append(parse_message(msg, f"{Path(path).name}#{i + 1}"))
    finally:
        box.close()
    return out


def read_msg(path: Path) -> dict:
    """Outlook .msg -> message dict. Raises msglib.MsgUnsupported (Dutch text) if it cannot be read."""
    f = msglib.read_msg_parts(Path(path))
    body = _clean(f["body"] or _html_to_text(f["html"]))
    names = [n for n, _c, _d in f["attachments"]]
    pdfs = [att.read_pdf_attachment(n, d) for n, c, d in f["attachments"] if att.is_pdf(n, c)]
    return {"id": _message_id(f["message_id"], f["from"], f["subject"], body), "from": f["from"],
            "from_name": f["from_name"], "to": f["to"], "subject": f["subject"], "date": f["date"], "body": body,
            "attachments": names, "pdfs": pdfs, "origin": Path(path).name}


def import_paths(paths: list[str | Path], problems: list[str] | None = None) -> list[dict]:
    """Accept .eml, .msg, .mbox files (or files without extension that start with 'From '), and folders.
    Files that cannot be read are skipped; a Dutch explanation is appended to `problems` if given."""
    msgs: list[dict] = []
    for p in map(Path, paths):
        if p.is_dir():
            msgs.extend(import_paths(sorted(x for x in p.iterdir() if x.is_file()), problems))
            continue
        suffix = p.suffix.lower()
        if suffix == ".eml":
            msgs.append(read_eml(p))
        elif suffix == ".msg":
            try:
                msgs.append(read_msg(p))
            except msglib.MsgUnsupported as exc:
                if problems is not None:
                    problems.append(f"{p.name}: {exc}")
        elif suffix in (".mbox", ".mbx") or (suffix == "" and p.read_bytes()[:5] == b"From "):
            msgs.extend(read_mbox(p))
    seen, unique = set(), []
    for m in msgs:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique
