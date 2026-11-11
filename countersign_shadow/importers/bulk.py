# SPDX-License-Identifier: Apache-2.0
"""Bulk mail import: a whole Gmail Takeout export (.zip or .mbox) or a folder with hundreds of .eml files.

A pilot user should not have to download 20-50 mails one by one. They may hand over a full mailbox instead;
this module picks the mails that are about money from the last few months and keeps the newest ones:

  * streams the mailbox (a Takeout mbox can be several GB; it is never read into memory at once);
  * reads only the headers first and skips mails older than `months` and Google chat logs;
  * keeps a mail if its subject or text is about money (invoice, payment, IBAN, refund, order, reminder...)
    or it has a PDF attachment;
  * keeps at most `limit` of those, newest first (at most SPAM_LIMIT from the spam folder; trash is skipped).
  * all exports handed over in one go (e.g. the parts of a multi-part Takeout) share one selection.

A small export (at most `small` mails, e.g. 30 mails the user picked by hand) is kept whole: no filter.
Nothing is sent anywhere.
"""
from __future__ import annotations

import heapq
import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesHeaderParser, BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import BinaryIO, Iterator

LIMIT = 50    # mails kept from one bulk export: enough for a 10-15 minute review
MONTHS = 6    # look-back window
SMALL = 60    # exports up to this many mails are kept whole

MONEY_RE = re.compile(
    r"(?i)(factu\w*|invoice\w*|beta+l\w*|betaald|\bpayment\w*|\bpaid\b|\bpay\b|\biban\b|rekeningnummer|"
    r"bankgegevens|bank ?details|overboek\w*|overmaken|overschrijv\w*|herinnering|reminder|aanmaning|incasso|"
    r"machtiging|terugbetal\w*|refund\w*|creditnota|credit note|bestelling|order\w*|offerte|\bbedrag\b|"
    r"\bamount\b|openstaand\w*|outstanding|vervaldatum|due date|€|\beur\b|euro|paypal|tikkie|\bwire\b|"
    r"bank transfer|rekening|account (?:number|details)|belastingdienst|\bcjib\b|\bboete\b|voldo\w*|"
    r"verschuldigd|kwitantie|declaratie|\bnota\b|\bbtw\b|transactie|stort\w*|creditcard|\bgeld\b|sommatie|"
    r"deurwaarder|spoed|dringend|urgent|vertrouwelijk|confidential|cadeaukaart|gift ?card)")
TRASH = {"trash", "prullenbak", "bin"}
SPAM = {"spam"}
SPAM_LIMIT = 10  # a few suspicious mails are welcome; a spam folder must not push real invoices out

_HEADERS = BytesHeaderParser(policy=policy.compat32)
_PARSER = BytesParser(policy=policy.default)


def iter_mbox(fh: BinaryIO) -> Iterator[bytes]:
    """Yield the raw bytes of each message in an mbox stream (separator: a 'From ' line at the start of the
    file or after an empty line). Works on file objects from open() and from zipfile."""
    buf: list[bytes] = []
    prev_blank = True
    for line in fh:
        if line.startswith(b"From ") and prev_blank:
            if buf:
                yield b"".join(buf)
            buf = []
        else:
            buf.append(line[1:] if line.startswith(b">From ") else line)
        prev_blank = line in (b"\n", b"\r\n")
    if buf:
        yield b"".join(buf)


def _date(value) -> datetime | None:
    try:
        d = parsedate_to_datetime(str(value)) if value else None
    except (TypeError, ValueError, IndexError):
        return None
    if d is not None and d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _quick_text(msg) -> tuple[str, bool]:
    """Subject + text parts (no attachment decoding) and whether the mail has a PDF attachment."""
    parts, has_pdf = [str(msg.get("Subject", ""))], False
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = (part.get_filename() or "").lower()
        ctype = part.get_content_type()
        if name.endswith(".pdf") or ctype == "application/pdf":
            has_pdf = True
            continue
        if ctype in ("text/plain", "text/html") and part.get_content_disposition() != "attachment":
            try:
                content = part.get_content()
            except (LookupError, ValueError):
                content = (part.get_payload(decode=True) or b"").decode("latin-1", "replace")
            if isinstance(content, str):
                parts.append(content[:20000])
    return "\n".join(parts), has_pdf


def is_money_mail(msg) -> bool:
    text, has_pdf = _quick_text(msg)
    return has_pdf or bool(MONEY_RE.search(text))


class Selector:
    """Feed raw messages one by one; finish() returns the chosen (raw, origin) pairs, newest first."""

    def __init__(self, limit: int = LIMIT, months: int = MONTHS, small: int = SMALL, now: datetime | None = None):
        self.limit, self.small = limit, small
        self.cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=round(months * 30.5))
        self.months = months
        self.seen = self.old = self.chat = self.trash = self.unrelated = self.money = 0
        self._first: list[tuple[bytes, str]] = []
        self._heap: list[tuple[float, int, bytes, str]] = []
        self._spam: list[tuple[float, int, bytes, str]] = []

    def feed(self, raw: bytes, origin: str) -> None:
        self.seen += 1
        if self.seen <= self.small:
            self._first.append((raw, origin))
        else:
            self._first = []
        hdr = _HEADERS.parsebytes(raw)
        labels = {x.strip().lower() for x in str(hdr.get("X-Gmail-Labels", "")).split(",")}
        if "chat" in labels or "chats" in labels:
            self.chat += 1
            return
        if labels & TRASH:
            self.trash += 1
            return
        when = _date(hdr.get("Date"))
        if when is not None and when < self.cutoff:
            self.old += 1
            return
        if not is_money_mail(_PARSER.parsebytes(raw)):
            self.unrelated += 1
            return
        self.money += 1
        item = (when.timestamp() if when else 0.0, self.seen, raw, origin)
        heap, cap = (self._spam, min(SPAM_LIMIT, self.limit)) if labels & SPAM else (self._heap, self.limit)
        if len(heap) < cap:
            heapq.heappush(heap, item)
        else:
            heapq.heappushpop(heap, item)

    @property
    def filtered(self) -> bool:
        return self.seen > self.small

    def finish(self) -> list[tuple[bytes, str]]:
        if not self.filtered:
            return list(self._first)
        newest = sorted(self._heap + self._spam, reverse=True)[:self.limit]
        return [(raw, origin) for _ts, _n, raw, origin in newest]

    @property
    def kept(self) -> int:
        return len(self._first) if not self.filtered else min(len(self._heap) + len(self._spam), self.limit)

    def summary_nl(self) -> str:
        if not self.filtered:
            return f"{self.seen} mails gevonden, allemaal ingelezen."
        skipped = [f"{self.old} ouder dan {self.months} maanden", f"{self.unrelated} niet over geld"]
        if self.trash:
            skipped.append(f"{self.trash} uit de prullenbak")
        if self.chat:
            skipped.append(f"{self.chat} chatbericht{'en' if self.chat != 1 else ''}")
        more = f" (de nieuwste {self.limit})" if self.money > self.limit else ""
        return (f"{self.seen} mails bekeken; {self.kept} mails over geld uit de laatste {self.months} maanden "
                f"gekozen{more}. Overgeslagen: {', '.join(skipped)}.")


def is_mbox_file(p: Path) -> bool:
    if p.suffix.lower() in (".mbox", ".mbx"):
        return True
    if p.suffix:
        return False
    try:
        with open(p, "rb") as fh:
            return fh.read(5) == b"From "
    except OSError:
        return False


def select_mbox(path: Path, sel: Selector) -> None:
    with open(path, "rb") as fh:
        for i, raw in enumerate(iter_mbox(fh)):
            sel.feed(raw, f"{path.name}#{i + 1}")


def select_zip(path: Path, sel: Selector, msg_files: list[Path], problems: list[str] | None,
               tmpdir: str) -> None:
    """Read the .mbox and .eml members of a zip (Google Takeout). .msg members are extracted to `tmpdir` and
    appended to `msg_files` (the .msg reader needs a file)."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        if problems is not None:
            problems.append(f"{path.name}: dit zip-bestand kan niet worden geopend ({exc}). Pak het opnieuw "
                            "in of download de export opnieuw.")
        return
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            low = name.lower()
            short = f"{path.name}/{name.rsplit('/', 1)[-1]}"
            if low.endswith((".mbox", ".mbx")):
                with zf.open(info) as fh:
                    for i, raw in enumerate(iter_mbox(fh)):
                        sel.feed(raw, f"{short}#{i + 1}")
            elif low.endswith(".eml"):
                sel.feed(zf.read(info), short)
            elif low.endswith(".msg"):
                out = Path(tmpdir) / f"{len(msg_files):05d}.msg"
                out.write_bytes(zf.read(info))
                msg_files.append(out)


def parse_raw(raw: bytes):
    return _PARSER.parsebytes(raw)


def new_tmpdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="countersign-zip-")
