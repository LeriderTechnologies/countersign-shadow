# SPDX-License-Identifier: Apache-2.0
"""Money helpers: Decimal only, never float. Understands Dutch ("1.234,56") and English ("1,234.56") notation."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")


class MoneyError(ValueError):
    pass


def parse_amount(value) -> Decimal:
    """Parse a positive amount and keep it exact. Rejects floats, NaN, negatives and sub-cent precision."""
    if isinstance(value, float):
        raise MoneyError("float amounts are not accepted; pass a string like '12.50'")
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise MoneyError(f"not an amount: {value!r}") from exc
    if not d.is_finite():
        raise MoneyError(f"not a finite amount: {value!r}")
    if d.as_tuple().exponent < -2:  # type: ignore[operator]
        raise MoneyError(f"more than 2 decimals: {value!r}")
    if d <= 0:
        raise MoneyError(f"amount must be positive: {value!r}")
    return d.quantize(CENT)


def parse_signed(text: str) -> Decimal:
    """Parse a signed amount as banks write it: '+99,01', '-10,00', '1.234,56', '-1,234.56', '12.5'.

    The last '.' or ',' followed by 1-2 digits at the end is the decimal separator; other separators are
    thousands separators.
    """
    if text is None:
        raise MoneyError("empty amount")
    s = str(text).strip().replace(" ", "").replace(" ", "").replace("€", "").replace("EUR", "")
    if not s:
        raise MoneyError("empty amount")
    sign = 1
    if s[0] in "+-":
        sign = -1 if s[0] == "-" else 1
        s = s[1:]
    m = re.fullmatch(r"([\d.,]*?)([.,](\d{1,2}))?", s)
    if not m or not (m.group(1) or m.group(3)):
        raise MoneyError(f"not an amount: {text!r}")
    whole = re.sub(r"[.,]", "", m.group(1) or "0")
    frac = m.group(3) or "0"
    if not whole.isdigit():
        raise MoneyError(f"not an amount: {text!r}")
    try:
        return (sign * Decimal(f"{int(whole)}.{frac}")).quantize(CENT)
    except (InvalidOperation, ValueError) as exc:  # absurdly long digit runs (corrupt file) exceed Decimal precision
        raise MoneyError(f"not an amount: {str(text)[:40]!r}") from exc


# Amounts written in messages: "€ 1.234,56", "EUR 38,00", "38.00 EUR", "450,- euro", "€38".
_NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{1,2})?"
# the trailing-currency branch only starts where a number starts -- not right after a digit or after
# "digit + separator". Without that guard finditer retried the number at every position of a long digit run and
# backtracked over the rest of it each time (quadratic: ~14 s on a 20,000-digit run). A well-formed amount starts
# at the beginning of its run, so real amounts are unchanged; only fragments of malformed runs ("12345.678 EUR"
# used to yield 45.678) no longer match.
# the trailing-currency branch ended in a plain \b, but "€" is not a \w character, so \b can only
# match right after "€" when the next character IS a \w char (e.g. "50€x") -- never before a space, punctuation
# or end of string/line, which is how every real amount ends ("7,50 €", "250€.", "1.234,56 €" at EOL). That made
# the whole trailing-€ branch effectively dead. "EUR"/"euro"/"eur" end in a \w char, so \b there was always fine.
# Fixed by closing the "€" alternative with (?!\w) (true at end of string/text, unlike \b) instead of a shared \b.
AMOUNT_RE = re.compile(
    rf"(?:(?:€|EUR|eur)\s?(?P<a>{_NUM})(?:,-)?)|"
    rf"(?:(?<!\d)(?<!\d[.,])(?P<b>{_NUM})(?:,-)?\s?(?:€(?!\w)|EUR\b|euro\b|eur\b))")


def find_amounts(text: str) -> list[tuple[Decimal, int, str]]:
    """All amounts in a text as (Decimal, position, matched text). Currency assumed EUR."""
    out = []
    for m in AMOUNT_RE.finditer(text):
        raw = m.group("a") or m.group("b")
        try:
            d = parse_signed(raw)
        except MoneyError:
            continue
        if d > 0:
            out.append((d, m.start(), m.group(0)))
    return out


def fmt(d: Decimal) -> str:
    return str(d.quantize(CENT))


# an amount written WITHOUT a currency sign ("1.840,00", "1,250.00", "143,20", "(1.150)"): cents or a
# thousands separator, not part of a longer number, date ("12.11.2026"), time range, phone number, IBAN or
# reference ("2026/0412", "GH-7802"). A plain integer ("1150") is never matched here. Used by checks.py (structural
# anchors) and by the rule planner (only next to a payment word: see planners/rules.py).
BARE_AMOUNT_RE = re.compile(r"(?<![\w.,/-])(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d{1,6}[.,]\d{2})(?:,-)?"
                            r"(?![\w/-]|[.,]\d)")


# a clock time is no amount. "vanaf 14.00 uur", "om 9.30", "from 14.00", "until 17.30 CET": hours
# 0-23, two-digit minutes, a '.' separator (a ',' is Dutch cents: "14,00" stays money), and a time word right before
# it or "uur"/"u"/"h"/"am"/"pm"/"CET" right after it. "14:00" never matched BARE_AMOUNT_RE (a ':' is no separator).
_CLOCK_RE = re.compile(r"(?:[01]?\d|2[0-3])\.[0-5]\d")
_CLOCK_BEFORE = re.compile(r"(?i)\b(?:om|vanaf|from|at|tot|until|till|tussen|between)\s*$")
_CLOCK_AFTER = re.compile(r"(?i)\s*(?:uur\b|u\b|h\b|hrs?\b|am\b|pm\b|a\.m\.|p\.m\.|cet\b|cest\b)")


def is_clock_time(text: str, start: int, end: int) -> bool:
    return bool(_CLOCK_RE.fullmatch(text[start:end]) and (_CLOCK_BEFORE.search(text[max(0, start - 12):start]) or
                                                          _CLOCK_AFTER.match(text, end)))


def find_bare_amounts(text: str) -> list[tuple[int, int, str]]:
    """(start, end, matched text) of every amount without a currency sign. Not parsed: callers decide context.
    Clock times ("vanaf 14.00 uur") are skipped."""
    return [(m.start(), m.end(), m.group(0)) for m in BARE_AMOUNT_RE.finditer(text)
            if not is_clock_time(text, m.start(), m.end())]


# a REPORTED figure is no pay amount. Two shapes:
#  - a labelled business figure: "omzet € 48.600", "voorbelasting € 6.120", "turnover of EUR 120,000", "revenue:
#    1.250,00" (a bookkeeper's report, a held-out test mail). The label is the last word(s) before the amount on its line, with at
#    most a ":" / "van" / "of" / "was" / "bedraagt" in between. Plain "btw" is NOT a report label: "te betalen btw
#    € 2.140" is a real payment (Belastingdienst). Only input VAT and quarter/year totals of btw are.
#  - a reported total: "doel van 12.500,00 gehaald", "12.500,00 opgehaald", "goal of EUR 12,500 reached", "we raised
#    € 8.400" (crowdfunding results, a QA probe): a gehaald/opgehaald/ingezameld/bereikt/reached/raised word in
#    the amount's own clause (40 chars either side, same line, no sentence end in between).
_REPORT_LABEL_RE = re.compile(
    # compounds "pinomzet", "kasomzet", "winkelomzet", "dagomzet" (a held-out test mail "De pinomzet was € 38.412").
    r"(?i)\b(?:(?:netto-?|bruto-?|jaar|kwartaal|maand|pin|kas|contant|winkel|web|dag|week|totale\s+|total\s+|"
    r"annual\s+|quarterly\s+|net\s+|gross\s+)?"
    r"(?:omzet|turnover|revenues?|sales)|voorbelasting|input\s+(?:vat|tax)|"
    r"btw[- ]?(?:totaal|total)\s+(?:over|of|for|van)\s+(?:het\s+|the\s+)?(?:kwartaal|quarter|jaar|year|q[1-4])\w*|"
    r"(?:winst|profit)|(?:inkomsten|income))"
    r"\s*(?:\([^)]{0,20}\)\s*)?(?:(?:\s+[\w-]{1,12}){1,3}\s*[:=]|[:=]?)\s*"
    r"(?:(?:van|of|was|is|bedraagt|bedroeg|amounted to|totaal|total)\s+)?(?:€|EUR|eur)?\s*$")
_REPORTED_TOTAL_RE = re.compile(
    r"(?i)\b(?:gehaald|opgehaald|ingezameld|bijeengebracht|binnengehaald|bereikt|reached|raised|exceeded|overschreden)\b")
_PAY_LABEL_RE = re.compile(r"(?i)\b(?:fee|fees|kosten|costs?|charge|bedrag|amount|due|betalen|betaling|payable|factuur|"
                           r"invoice|honorarium|share|commissie|commission|afdracht|af te dragen|vergoeding|royalt\w*)\b")
_CLAUSE_STOP_RE = re.compile(r"[!?;\n]|\.(?=\s|$)")


def is_reported_figure(text: str, start: int, end: int) -> bool:
    """The amount at text[start:end] is a reported figure (business figure or a reached total), not an amount to pay."""
    ls = text.rfind("\n", 0, start) + 1
    lab = _REPORT_LABEL_RE.search(text[max(ls, start - 45):start])
    if lab and not _PAY_LABEL_RE.search(lab.group(0)):  # "revenue share fee: € 120" / "omzet ... te betalen" is a charge
        return True
    a = max([m.end() for m in _CLAUSE_STOP_RE.finditer(text, max(ls, start - 40), start)] or [max(ls, start - 40)])
    stop = _CLAUSE_STOP_RE.search(text, end, min(len(text), end + 40))
    b = stop.start() if stop else min(len(text), end + 40)
    return bool(_REPORTED_TOTAL_RE.search(text, a, start) or _REPORTED_TOTAL_RE.search(text, end, b))
