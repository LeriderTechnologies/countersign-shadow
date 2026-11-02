# SPDX-License-Identifier: Apache-2.0
"""Official payees: a small, curated table of Dutch public bodies whose IBANs fraudsters imitate.

Every IBAN here was copied from the body's own website (URL + date below). Nothing is taken from news sites,
accounting blogs or memory. If an IBAN could not be read on an official page, it is NOT in this table.

Used by check C13 (checks.py): a message that claims to come from one of these payees but asks for an IBAN that
is not on its official list gets a strong warning; an IBAN that IS on the list gets a reassurance line and counts
as known for C5 (like an IBAN you vetted yourself in policy.json).

`complete=True` means the official page presents its list as complete ("De volgende rekeningnummers zijn van ons",
"Staat het rekeningnummer er niet bij? Maak het geld niet over"), so a mismatch is a block. Otherwise a mismatch
is a warning only, because the body may use accounts that its page does not list.

Re-check these pages before relying on the table after its `checked` date. Run:
    python3 -m unittest tests.test_official_payees
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import iban as ibanlib

BD_LIST_URL = ("https://www.belastingdienst.nl/wps/wcm/connect/nl/contact/content/"
               "alle-rekeningnummers-van-de-belastingdienst")
BD_OLD_ING_URL = ("https://www.belastingdienst.nl/wps/wcm/connect/nl/nieuwe-huisbank/content-nieuwe-huisbank/"
                  "betalen-oud-rekeningnummer")
TOESLAGEN_URL = "https://www.belastingdienst.nl/wps/wcm/connect/nl/bank-toeslagen/nieuw-rekeningnummer-dienst-toeslagen"
DUO_URL = "https://duo.nl/particulier/rekeningnummers.jsp"
CJIB_URL = "https://www.cjib.nl/nl-b/rekeningnummers"
CHECKED = "2026-10-27"


@dataclass(frozen=True)
class OfficialPayee:
    key: str
    name: str
    claim_re: re.Pattern          # matches a payee / sender / supplier name that claims to be this body
    domain_tokens: tuple          # alphanumeric tokens that, inside the sender's domain, claim this body
    ibans: dict                   # IBAN -> note
    source: str
    checked: str
    complete: bool
    what_to_do: str               # Dutch: what the official page says to do when an IBAN is not on the list
    sources: tuple = field(default=())


def _ibans(note: str, *raw: str) -> dict:
    return {ibanlib.normalise(r): note for r in raw}


_BD_NEW = _ibans("Rabobank, sinds 1 mei 2026",
                 "NL04 RABO 0200 1122 44", "NL04 RABO 0200 1152 51", "NL04 RABO 0200 1444 48",
                 "NL04 RABO 0200 1445 45", "NL04 RABO 0200 1816 96", "NL04 RABO 0200 1818 90",
                 "NL04 RABO 0200 1841 21", "NL04 RABO 0200 1966 34")
_BD_OLD = _ibans("oud ING-rekeningnummer (tot 1 mei 2026); de Belastingdienst verwerkt betalingen hierop nog",
                 "NL86 INGB 0002 4455 88", "NL04 INGB 0000 3615 64", "NL09 INGB 0000 9400 79",
                 "NL11 INGB 0000 0048 66", "NL12 INGB 0000 0048 48", "NL26 INGB 0000 4412 90",
                 "NL28 INGB 0000 5243 83", "NL36 INGB 0003 4455 88", "NL40 INGB 0000 7346 57",
                 "NL41 INGB 0000 7567 55", "NL42 INGB 0000 0649 33", "NL43 INGB 0000 1908 21",
                 "NL49 INGB 0000 4410 70", "NL51 INGB 0000 0684 57", "NL61 INGB 0000 9401 13",
                 "NL72 INGB 0000 3617 95", "NL72 INGB 0000 9401 09", "NL73 INGB 0000 0048 17",
                 "NL73 INGB 0000 9400 91", "NL78 INGB 0000 4440 40", "NL78 INGB 0000 9400 01",
                 "NL85 INGB 0000 0048 92", "NL88 INGB 0000 1451 00", "NL88 INGB 0000 4410 47",
                 "NL89 INGB 0000 0048 20", "NL96 INGB 0000 0047 91", "NL96 INGB 0000 1289 51")

OFFICIAL_PAYEES: tuple[OfficialPayee, ...] = (
    OfficialPayee(
        key="belastingdienst", name="Belastingdienst",
        claim_re=re.compile(r"(?i)\bbelasting\s?dienst\b|\btax (?:office|authority)\b"),
        domain_tokens=("belastingdienst",),
        ibans={**_BD_NEW, **_BD_OLD}, source=BD_LIST_URL, checked=CHECKED, complete=True,
        what_to_do="Maak het geld niet over, maar bel de BelastingTelefoon: 0800 - 0543 (vanuit het buitenland "
                   "+31 555 385 385).",
        sources=(BD_LIST_URL, BD_OLD_ING_URL)),
    OfficialPayee(
        key="toeslagen", name="Dienst Toeslagen",
        claim_re=re.compile(r"(?i)\bdienst\s?toeslagen\b"),
        domain_tokens=("toeslagen",),
        ibans=_ibans("Rabobank, sinds 1 mei 2026", "NL04 RABO 0200 1122 44"), source=TOESLAGEN_URL,
        checked=CHECKED, complete=False,
        what_to_do="Neem het rekeningnummer over uit de brief of betaalinformatie in Mijn toeslagen, of bel de "
                   "BelastingTelefoon: 0800 - 0543."),
    OfficialPayee(
        key="duo", name="DUO (Dienst Uitvoering Onderwijs)",
        claim_re=re.compile(r"(?i)\bdienst uitvoering onderwijs\b|"
                            r"^\s*duo(?:\s+(?:groningen|studieschuld|studiefinanciering|lesgeld|incasso))?\s*$"),
        domain_tokens=(),  # "duo" is too short to match safely inside a domain
        ibans={**_ibans("studieschuld, lesgeld, betalingsregeling", "NL45 INGB 0705 0019 03"),
               **_ibans("lerarenbeurs", "NL04 INGB 0705 0018 65")},
        source=DUO_URL, checked=CHECKED, complete=False,
        what_to_do="Betaal via iDEAL | Wero in Mijn DUO of neem het rekeningnummer over uit het betalingsverzoek "
                   "van DUO."),
    OfficialPayee(
        key="cjib", name="CJIB (Centraal Justitieel Incassobureau)",
        claim_re=re.compile(r"(?i)\bcjib\b|\bcentraal justitieel incasso\s?bureau\b"),
        domain_tokens=("cjib",),
        ibans={**_ibans("verkeersboete (letter M op de brief)", "NL56 INGB 0705 0051 00"),
               **_ibans("strafbeschikking (O)", "NL90 INGB 0705 0050 70"),
               **_ibans("boete niet hebben van een zorgverzekering (Z)", "NL84 INGB 0705 0052 75"),
               **_ibans("door de rechter opgelegde boete (S)", "NL34 INGB 0705 0055 93")},
        source=CJIB_URL, checked="2026-10-28", complete=False,
        what_to_do="Neem het rekeningnummer over uit de brief van het CJIB (de letter op de brief hoort bij één "
                   "rekeningnummer) of controleer het op cjib.nl/nl-b/rekeningnummers. Betaal altijd ten name "
                   "van CJIB."),
)


def lookup(iban: str | None) -> tuple[OfficialPayee, str] | None:
    """(payee, note) if this IBAN is on an official list, else None. The first listed payee wins."""
    ib = ibanlib.normalise(iban)
    for p in OFFICIAL_PAYEES:
        if ib in p.ibans:
            return p, p.ibans[ib]
    return None


_TNV_RE = re.compile(r"(?i)\b(?:t\.?\s?n\.?\s?v\.?|ten name van|in the name of)\s*:?\s*(?:de\s+)?([^\n,.;]{3,60})")


def claimed_by(names: list[str], domain: str = "", text: str = "") -> list[OfficialPayee]:
    """Official payees this message claims to come from or to be paid to: a payee / sender / supplier name, the
    sender's domain, or a "t.n.v. <name>" line in the text. A body that merely mentions the Belastingdienst (a
    bookkeeper's note about your tax return) does not count as a claim."""
    cands = [n for n in names if n] + [m.group(1) for m in _TNV_RE.finditer(text or "")]
    dom = re.sub(r"[^a-z0-9]", "", (domain or "").lower())
    out = []
    for p in OFFICIAL_PAYEES:
        if any(p.claim_re.search(n) for n in cands) or any(t in dom for t in p.domain_tokens):
            out.append(p)
    return out
