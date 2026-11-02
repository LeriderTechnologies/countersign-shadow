# SPDX-License-Identifier: Apache-2.0
"""Shared Hypothesis strategies: structure-aware "plausible Dutch bank export" building blocks.

Everything produced here is synthetic test data -- invented names, invented account numbers, *.example
domains. IBANs are real-shaped (valid mod-97 checksum) but the underlying account numbers are random.
"""
from __future__ import annotations

from datetime import date

from hypothesis import strategies as st

NL_BANKS = ["INGB", "RABO", "ABNA", "BUNQ", "TRIO", "KNAB", "SNSB", "ASNB", "FVLB", "RBRB"]
# A couple of other SEPA countries, for a bit of non-NL coverage (BBAN length must match LENGTHS[country] - 4).
OTHER_COUNTRIES = {"BE": 12, "DE": 18}

# cp1252-safe "interesting" characters (also valid in utf-8): accented Latin, euro sign, en/em dash, quotes.
CP1252_SAFE_EXTRA = "éèëïóäüÄÖÜñç€–—''\"\""
FIRST_NAMES = ["Sanne", "Pieter", "Marieke", "Lena", "Tom", "Ana", "Kees", "Fatima", "Youssef", "Anneke"]
COMPANY_WORDS = ["Kartonfabriek", "Glazuurhandel", "Studio", "Administratiekantoor", "Ovenbouw",
                 "Verpakking", "Klei", "Noord", "Design", "Techniek", "Media", "Groothandel"]
LEGAL_FORMS = ["B.V.", "BV", "V.O.F.", "N.V.", ""]


def _mod97_check(country: str, bban: str) -> str:
    """Generic ISO 13616 mod-97 check digits for any (country, bban) -- same formula as iban.make_nl,
    parameterised so it also works for BE/DE test IBANs, not only NL."""
    digits = "".join(str(int(c, 36)) for c in bban.upper() + country + "00")
    return f"{98 - int(digits) % 97:02d}"


@st.composite
def st_iban(draw) -> str:
    if draw(st.integers(0, 9)) == 0:  # ~10% a non-NL SEPA IBAN, for a little format diversity
        country = draw(st.sampled_from(sorted(OTHER_COUNTRIES)))
        bban_len = OTHER_COUNTRIES[country]
        bban = draw(st.text(alphabet="0123456789", min_size=bban_len, max_size=bban_len))
        return f"{country}{_mod97_check(country, bban)}{bban}"
    bank = draw(st.sampled_from(NL_BANKS))
    account = draw(st.integers(0, 9_999_999_999))
    bban = f"{bank}{account:010d}"
    return f"NL{_mod97_check('NL', bban)}{bban}"


@st.composite
def st_amount_cents(draw, min_cents: int = 1, max_cents: int = 250_000_00) -> int:
    return draw(st.integers(min_value=min_cents, max_value=max_cents))


def cents_to_str(cents: int, decimal_sep: str = ".") -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}{decimal_sep}{cents % 100:02d}"


@st.composite
def st_date(draw) -> date:
    return draw(st.dates(min_value=date(2019, 1, 1), max_value=date(2027, 12, 31)))


#: Letters/digits/punctuation that are safe in ALL of {ascii, utf-8, utf-8-sig, cp1252} *and* in the
#: tiny PDF text-layer writer tests/fixtures.py:make_pdf() reuses (which itself only knows cp1252). Kept
#: deliberately narrow for the structure-aware "valid file" generators, where round-trip correctness must
#: hold regardless of which encoding gets picked; wide/exotic Unicode is a mutation-leg concern instead.
TEXT_ALPHABET = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,-:" + CP1252_SAFE_EXTRA)


@st.composite
def st_name(draw) -> str:
    """A plausible counterparty name: 1-2 words, optional legal form, occasional cp1252-safe accent."""
    n = draw(st.integers(1, 2))
    words = draw(st.lists(st.sampled_from(COMPANY_WORDS + FIRST_NAMES), min_size=n, max_size=n))
    name = " ".join(words)
    if draw(st.booleans()):
        name += " " + draw(st.sampled_from(LEGAL_FORMS))
    if draw(st.integers(0, 4)) == 0:
        name += draw(st.sampled_from(list(CP1252_SAFE_EXTRA)))
    return name.strip() or "X"


@st.composite
def st_description(draw, max_len: int = 80) -> str:
    """A free-text description: normally short, occasionally very long (stresses truncation/wrapping)."""
    long = draw(st.integers(0, 20)) == 0
    size = draw(st.integers(0, 4000 if long else max_len))
    text = draw(st.text(alphabet=st.sampled_from(list(TEXT_ALPHABET)), min_size=0, max_size=size))
    return " ".join(text.split())  # collapse whitespace -- matches what every parser does on the way out


@st.composite
def st_encoding(draw) -> str:
    return draw(st.sampled_from(["utf-8", "utf-8-sig", "cp1252"]))


@st.composite
def st_newline(draw) -> str:
    # Bare "\r" (classic Mac) is deliberately excluded: io.StringIO's line splitting (what csv.DictReader
    # reads through) only recognises "\n", so a \r-only file reads back as one giant unquoted line --
    # a real difference from "\r\n"/"\n", but one in the *test harness's* file-reading plumbing, not
    # something any NL bank export tool produces in 2026. CRLF and LF alone are the real-world axis.
    return draw(st.sampled_from(["\n", "\r\n"]))


def encode_text(text: str, encoding: str) -> bytes:
    """Encode, degrading non-cp1252-safe characters with 'replace' the way a real bank export would
    rather than crashing the generator itself (the importer side still sees genuine mojibake sometimes)."""
    errors = "replace" if encoding == "cp1252" else "strict"
    raw = text.encode(encoding.replace("-sig", ""), errors=errors)
    if encoding == "utf-8-sig":
        raw = b"\xef\xbb\xbf" + raw
    return raw
