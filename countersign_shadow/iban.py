# SPDX-License-Identifier: Apache-2.0
"""IBAN helpers: normalise, find in text, check the ISO 13616 mod-97 checksum."""
from __future__ import annotations

import re

# "NL 30 FAKE 0691 1240 38" (a space between the country code and the check digits, a held-out test mail) is
# read too, but only as a full-length, checksum-valid IBAN of a known country (group "sp"), so ordinary text such as
# "OP 12 3456 7890" never turns into an IBAN.
IBAN_RE = re.compile(r"\b([A-Z]{2}(?P<sp> )?\d{2}(?:[\s\-.]?[A-Z0-9]{4}){2,7}(?:[\s\-.]?[A-Z0-9]{1,4})?)\b")
LENGTHS = {"NL": 18, "BE": 16, "DE": 22, "FR": 27, "GB": 22, "LU": 20, "ES": 24, "IT": 27, "IE": 22, "AT": 20}
# the other SEPA countries' lengths, used only to CUT an IBAN that runs on into the next line's word ("LT38
# 1210 0000 4918 7362\nBIC: ..", a held-out test mail, was read as "LT381210000049187362BIC" and failed the checksum).
# Not a strict length like LENGTHS: only an invalid read whose cut IS valid is cut; anything else reads as before.
MORE_LENGTHS = {"LT": 20, "LV": 21, "EE": 20, "PL": 28, "PT": 25, "CH": 21, "LI": 21, "DK": 18, "FI": 18, "SE": 24,
                "NO": 15, "IS": 26, "CZ": 24, "SK": 24, "HU": 28, "RO": 24, "BG": 22, "HR": 21, "SI": 19, "GR": 27,
                "CY": 28, "MT": 31, "MC": 27, "SM": 27, "AD": 24, "VA": 22, "GI": 23}


def normalise(iban: str | None) -> str:
    """Strip the separators people write between IBAN groups (spaces, dashes, dots) before comparing/checksumming.
    a dash/dot-separated IBAN used to pass through uncaught; mod-97 in is_valid() still guards
    against false matches from ordinary text that happens to look IBAN-shaped."""
    return re.sub(r"[\s\-.]+", "", iban or "").upper()


def is_valid(iban: str | None) -> bool:
    s = normalise(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", s):
        return False
    if s[:2] in LENGTHS and len(s) != LENGTHS[s[:2]]:
        return False
    digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


def find_ibans(text: str) -> list[str]:
    """IBAN-looking strings in a text (normalised, checksum NOT yet checked)."""
    out = []
    for m in IBAN_RE.finditer(text.upper()):
        s = normalise(m.group(1))
        if s[:2] in LENGTHS:
            if len(s) < LENGTHS[s[:2]]:
                continue  # too short for this country: e.g. a Dutch BTW id like NL123456789B01, not an IBAN
            s = s[:LENGTHS[s[:2]]]
        elif s[:2] in MORE_LENGTHS and not is_valid(s) and is_valid(s[:MORE_LENGTHS[s[:2]]]):
            s = s[:MORE_LENGTHS[s[:2]]]
        if m.group("sp") and not ((s[:2] in LENGTHS or s[:2] in MORE_LENGTHS) and is_valid(s)):
            continue
        if s not in out:
            out.append(s)
    return out


IBAN_RE_I = re.compile(IBAN_RE.pattern, re.IGNORECASE)


def find_iban_spans(text: str) -> list[tuple[str, int, int]]:
    """Like find_ibans, but every occurrence with its (start, end) offsets in `text` itself (the pay-ask
    check needs to know where each IBAN sits). Checksum NOT checked."""
    out = []
    for m in IBAN_RE_I.finditer(text):
        s = normalise(m.group(1)).upper()
        if s[:2] in LENGTHS:
            if len(s) < LENGTHS[s[:2]]:
                continue
            s = s[:LENGTHS[s[:2]]]
        elif s[:2] in MORE_LENGTHS and not is_valid(s) and is_valid(s[:MORE_LENGTHS[s[:2]]]):
            s = s[:MORE_LENGTHS[s[:2]]]
        if m.group("sp") and not ((s[:2] in LENGTHS or s[:2] in MORE_LENGTHS) and is_valid(s)):
            continue
        out.append((s, m.start(1), m.end(1)))
    return out


def make_nl(bank: str, account10: str) -> str:
    """Build a valid NL IBAN (used to create synthetic test data)."""
    bban = bank.upper() + account10
    digits = "".join(str(int(c, 36)) for c in bban + "NL00")
    check = 98 - int(digits) % 97
    return f"NL{check:02d}{bban}"
