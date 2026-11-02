# SPDX-License-Identifier: Apache-2.0
"""PDF invoice attachments: extract the text layer and pull out IBAN, amounts, invoice number, supplier, KvK, BTW.

Uses the vendored pypdf (BSD-3-Clause, pure Python, countersign_shadow/_vendor). If that is missing, or for a scanned / image-only PDF, the
attachment gets a Dutch warning instead of text; nothing is guessed. There is no OCR.

Nothing here opens a connection or runs anything embedded in the PDF; pypdf only parses the file.
"""
from __future__ import annotations

import io
import logging
import re

from .. import iban as ibanlib
from .._vendor import load
from ..money import find_amounts, fmt

MAX_PDF_BYTES = 10_000_000
MAX_PAGES = 10
MAX_TEXT = 6000  # characters of PDF text kept per attachment


INVOICE_NO_RE = re.compile(
    r"(?i)\b(?:factuur(?:nummer|nr\.?)?|invoice(?:\s*(?:no\.?|number|nr\.?|#))?|nota(?:nummer)?)"
    r"\s*[:#]?\s*((?=[A-Z0-9/-]*\d)[A-Z0-9][A-Z0-9/-]{2,})")
KVK_RE = re.compile(r"(?i)\b(?:kvk|k\.v\.k\.|kamer van koophandel)(?:[- ]?(?:nummer|nr\.?))?\s*[:.]?\s*(\d{8})\b")
BTW_RE = re.compile(r"(?i)\b(NL\s?\d{9}\s?B\s?\d{2})\b")
TNV_RE = re.compile(r"(?i)\bt\.?\s?n\.?\s?v\.?\s*:?\s*([^\n,]{3,60})")
LEGAL_FORM_RE = re.compile(r"(?i)\b(b\.?v\.?|v\.?o\.?f\.?|n\.?v\.?|eenmanszaak|gmbh|ltd|bvba|srl)(?:\W|$)")
TOTAL_RE = re.compile(r"(?i)(totaal|te betalen|total|amount due|balance due)")
NOT_A_NAME = re.compile(r"(?i)^(factuur|invoice|nota|creditnota|pagina|page|datum|date)\b")


def is_pdf(filename: str | None, content_type: str | None) -> bool:
    return (content_type or "").lower() == "application/pdf" or (filename or "").lower().endswith(".pdf")


def pdf_text(data: bytes) -> tuple[str, str]:
    """Return (text, warning). Exactly one of the two is non-empty."""
    if not data:
        return "", "lege bijlage"
    if len(data) > MAX_PDF_BYTES:
        return "", f"bestand groter dan {MAX_PDF_BYTES // 1_000_000} MB, niet gelezen"
    if not data.lstrip()[:5].startswith(b"%PDF"):
        return "", "geen geldig PDF-bestand"
    try:
        PdfReader = load("pypdf").PdfReader
    except ImportError:
        return "", "pypdf ontbreekt (meegeleverde kopie in countersign_shadow/_vendor)"
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return "", "PDF is beveiligd met een wachtwoord"
        parts = []
        for page in reader.pages[:MAX_PAGES]:
            parts.append(page.extract_text() or "")
    except Exception as exc:  # pypdf raises many kinds; a broken attachment must never stop the import
        return "", f"PDF kon niet gelezen worden ({type(exc).__name__})"
    text = re.sub(r"[ \t]+", " ", "\n".join(parts)).strip()
    if len(re.sub(r"\W", "", text)) < 10:
        return "", "geen tekst in de PDF (gescand of een afbeelding?); tekstherkenning (OCR) doen we niet"
    return text[:MAX_TEXT], ""


def _supplier(text: str) -> str:
    m = TNV_RE.search(text)
    if m:
        return m.group(1).strip(" .:")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines[:15]:
        if LEGAL_FORM_RE.search(ln) and len(ln) <= 60 and not NOT_A_NAME.match(ln):
            return ln
    for ln in lines[:5]:
        if not NOT_A_NAME.match(ln) and re.search(r"[A-Za-z]{3}", ln) and len(ln) <= 60:
            return ln
    return ""


def _total(text: str, amounts: list) -> str:
    """The amount right after 'totaal' / 'te betalen' / 'total due'; the last such hint wins (totals come last)."""
    hit = None
    for d, pos, _raw in amounts:
        line_start = text.rfind("\n", 0, pos) + 1
        if TOTAL_RE.search(text[line_start:pos][-40:]):
            hit = d
    if hit is None and amounts:
        hit = max(a[0] for a in amounts)
    return fmt(hit) if hit is not None else ""


def parse_invoice(text: str) -> dict:
    """Invoice fields found in PDF text. IBANs are split into checksum-valid and invalid ones."""
    found = ibanlib.find_ibans(text)
    amounts = find_amounts(text)
    inv = INVOICE_NO_RE.search(text)
    kvk = KVK_RE.search(text)
    btw = BTW_RE.search(text)
    return {
        "ibans": [i for i in found if ibanlib.is_valid(i)],
        "bad_ibans": [i for i in found if not ibanlib.is_valid(i) and len(i) == ibanlib.LENGTHS.get(i[:2], -1)],
        "amounts": [fmt(d) for d, *_ in amounts],
        "total": _total(text, amounts),
        "invoice_ref": inv.group(1) if inv else "",
        "supplier": _supplier(text),
        "kvk": kvk.group(1) if kvk else "",
        "btw": re.sub(r"\s", "", btw.group(1)).upper() if btw else "",
    }


def read_pdf_attachment(name: str, data: bytes) -> dict:
    text, warning = pdf_text(data)
    out = {"name": name, "text": text, "warning": warning}
    out.update(parse_invoice(text) if text else
               {"ibans": [], "bad_ibans": [], "amounts": [], "total": "", "invoice_ref": "", "supplier": "",
                "kvk": "", "btw": ""})
    return out
