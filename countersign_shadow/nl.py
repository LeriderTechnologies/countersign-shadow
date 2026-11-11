# SPDX-License-Identifier: Apache-2.0
"""Dutch display helpers for the pages a pilot user sees (review page, start window, summary).

Amounts as "€ 1.250,00" (nl-NL), dates as "22 oktober 2026", and plain-Dutch names for the checks. The internal rule
ids ("W1 no pressure language", "C5 ...") stay in the cards, the code and the logs; only the pages use these labels.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MONTHS = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober",
          "november", "december")


def bedrag(x) -> str:
    """'96.80' / Decimal('1250') / 96.8 -> '€ 96,80' / '€ 1.250,00'. Anything that is no number comes back as text."""
    if x is None or x == "":
        return ""
    try:
        d = Decimal(str(x).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return str(x)
    if not d.is_finite():
        return str(x)
    s = f"{abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {'-' if d < 0 else ''}{s}"


def bedragen(xs) -> str:
    return ", ".join(bedrag(x) for x in xs)


_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?\s*$")


def datum(x) -> str:
    """'2026-10-22' -> '22 oktober 2026'; '2026-10-22T09:15:00+02:00' -> '22 oktober 2026, 09:15'.
    A date/datetime works too. Anything else (already Dutch, unknown layout) comes back unchanged."""
    if isinstance(x, datetime):
        return f"{x.day} {MONTHS[x.month - 1]} {x.year}, {x:%H:%M}"
    if isinstance(x, date):
        return f"{x.day} {MONTHS[x.month - 1]} {x.year}"
    s = "" if x is None else str(x)
    m = _ISO.match(s)
    if not m:
        return s
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return s
    out = f"{d} {MONTHS[mo - 1]} {y}"
    if m.group(4) and (m.group(4), m.group(5)) != ("00", "00"):
        out += f", {m.group(4)}:{m.group(5)}"
    return out


# What each check looks at, in plain Dutch: (text when the check passes, text when it fails).
RULE_NL = {
    "C1": ("Het bedrag is een normaal bedrag", "Er is geen geldig bedrag"),
    "C2": ("Het bedrag is in euro", "Het bedrag is niet in euro"),
    "C3": ("Het bedrag blijft onder de grens", "Het bedrag is hoger dan de grens"),
    "C4": ("Het rekeningnummer is goed opgebouwd", "Het rekeningnummer kan niet bestaan"),
    "C5": ("Je hebt eerder naar dit rekeningnummer betaald", "Je hebt nog nooit naar dit rekeningnummer betaald"),
    "C6": ("De factuur is nog niet betaald", "Deze factuur lijkt al betaald"),
    "C7": ("De klant vraagt niet meer terug dan hij betaalde",
           "De klant vraagt meer terug dan hij betaalde (of er is geen betaling gevonden)"),
    "C8": ("Je hebt deze PayPal-ontvanger eerder betaald", "Je hebt deze PayPal-ontvanger nog nooit betaald"),
    "C9": ("De ontvanger heeft een geldig e-mailadres", "De ontvanger heeft geen geldig e-mailadres"),
    "C10": ("De klant heeft nog niet betaald", "De klant heeft al betaald"),
    "C11": ("Geen andere kaart voor deze betaling", "Deze betaling staat al op een andere kaart"),
    "C12": ("De naam past bij je eerdere betalingen aan dit rekeningnummer",
            "De naam past niet bij je eerdere betalingen aan dit rekeningnummer"),
    "C13": ("Rekeningnummer staat op de officiële lijst van de instantie",
            "Rekeningnummer staat niet op de officiële lijst van de instantie"),
    "C14": ("Je kent de rekeningnummers in het bericht van eerdere betalingen",
            "Het bericht noemt een rekeningnummer dat je niet kent"),
    "C15": ("Deze betaling is nog niet eerder gevraagd", "Deze betaling is al eerder gevraagd in een ander bericht"),
    "P1": ("Het rekeningnummer op de PDF is hetzelfde als in de mail",
           "Het rekeningnummer op de PDF is anders dan in de mail"),
    "P2": ("Je kent het rekeningnummer op de PDF van eerdere betalingen",
           "Je hebt nog nooit naar het rekeningnummer op de PDF betaald"),
    "P3": ("Het rekeningnummer op de PDF is goed opgebouwd", "Het rekeningnummer op de PDF kan niet bestaan"),
    "P4": ("Het totaal op de PDF is hetzelfde als het gevraagde bedrag",
           "Het totaal op de PDF is anders dan het gevraagde bedrag"),
    "V1": ("Wat Countersign aanhaalt, staat echt in het bericht", "Wat Countersign aanhaalt, staat niet letterlijk in het bericht"),
    "V2": ("Het bedrag staat in het bericht", "Het bedrag staat niet in het bericht"),
    "V3": ("Het rekeningnummer staat in het bericht", "Het rekeningnummer staat niet in het bericht"),
    "V4": ("Het e-mailadres van de ontvanger staat in het bericht",
           "Het e-mailadres van de ontvanger staat niet in het bericht"),
    "W1": ("Geen druk of haast in het bericht", "Het bericht zet je onder druk of maakt haast"),
    "W2": ("Geen nieuwe bankgegevens in het bericht", "Het bericht meldt nieuwe bankgegevens"),
    "W3": ("De PDF-bijlagen zijn leesbaar", "Een PDF-bijlage is niet leesbaar"),
    "W4": ("Geen verdachte link of vraag om in te loggen",
           "Verdachte link of vraag om in te loggen of je gegevens in te vullen"),
}


def rule_id(rule: str) -> str:
    return (rule or "").split(" ", 1)[0]


def regel(rule: str, ok: bool = True) -> str:
    """'W1 no pressure language' -> 'Geen druk of haast in het bericht' (passed) or 'Het bericht zet je onder druk of
    maakt haast' (failed). An unknown rule gets a neutral label: the English name never reaches the page."""
    pair = RULE_NL.get(rule_id(rule))
    if not pair:
        return "Extra controle: in orde" if ok else "Extra controle: niet in orde"
    return pair[0] if ok else pair[1]


def planner(name: str) -> str:
    n = (name or "").lower()
    if n.startswith("rules"):
        return "vaste regels, op deze computer"
    if n.startswith("claude"):
        return "Claude (AI, via internet)"
    if n.startswith("local"):
        return "taalmodel op deze computer"
    return "taalmodel"


VERDICT_KORT = {"ok": "doorgelaten", "warn": "doorgelaten met waarschuwing", "blocked": "tegengehouden",
                "flagged": "verdacht", "none": "geen betaling nodig"}
ACTION_KORT = {"pay_invoice": "factuur betalen", "refund": "terugbetalen", "payout": "betalen via PayPal",
               "send_invoice": "factuur sturen", "send_reminder": "herinnering sturen",
               "flag_suspicious": "verdacht bericht", "no_action": "geen betaling nodig"}
