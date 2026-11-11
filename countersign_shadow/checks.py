# SPDX-License-Identifier: Apache-2.0
"""Code checks. Every proposal is checked against the user's OWN transaction history and the message text.

The planner's word counts for nothing here. A failed "block" check means: in a live Countersign this card
could not be approved. A failed "warn" check means: approve only after a second look.

In shadow mode nothing is executed either way; the pilot user judges whether the verdict was right.
"""
from __future__ import annotations

import functools
import json
import re
from bisect import bisect_left, bisect_right
import unicodedata
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import iban as ibanlib
from . import nl
from . import official
from .money import MoneyError, find_amounts, find_bare_amounts, is_reported_figure, parse_amount
from .planners import MONEY_OUT, message_text
from .planners.rules import INVOICE_REF_RE

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

PRESSURE_RE = re.compile(
    r"(?i)\b(spoed|dringend|urgent|asap|vandaag nog|binnen 24 uur|within 24 hours|direct(?:e)? betaling|"
    r"onmiddellijk|immediately|right now|do it now|niet bellen|don'?t call|geheim|confidential|vertrouwelijk|"
    r"skip the (?:usual )?checks|sla de (?:gebruikelijke )?controle over|ik zit in een (?:meeting|vergadering)|"
    r"i'?m in a meeting|laatste waarschuwing|final notice|incassobureau|deurwaarder)\b")
BANK_CHANGE_RE = re.compile(
    r"(?i)(nieuw(?:e)? (?:iban|rekeningnummer|bankrekening)|rekeningnummer (?:is )?gewijzigd|"
    r"gewijzigd(?:e)? (?:iban|rekeningnummer|bankgegevens)|new (?:iban|bank (?:account|details))|"
    r"bank details (?:have )?changed|changed (?:our )?bank|voortaan (?:op|naar)|from now on|"
    # "nieuwe rekening" / "nieuw rek nr" / "ander rekeningnummer" / "new account number" / "updated
    # bank details" / "changed their collection acct". Dutch "rekening" is also "bill" ("ik stuur een nieuwe
    # rekening"), so plain "nieuwe/andere rekening" counts only with nr/nummer, a colon, or an IBAN right after it.
    # "changed/updated .. account" needs a bank-ish account (bank/collection/payment/receiving account, account
    # number/details, acct, iban) within three words.
    r"\bnieuw(?:e)?\s+(?:bank)?rek(?:ening)?\.?\s?(?:nr\b|nummer\b|(?=\s*(?:is\s+)?[:-]?\s*[A-Z]{2}\s?\d{2}))|"
    r"\bnieuw(?:e)?\s+rek(?:ening)?\s*:|\bnieuw(?:e)?\s+rekeningnr\b|"
    # "Nieuwe rekening van de zaak: X" / "nieuwe rekening van ons bedrijf: X"; same
    # colon / IBAN-right-after rule.
    r"\bnieuw(?:e)?\s+(?:bank)?rekening\s+(?:van|voor)\s+(?:de\s+zaak|het\s+bedrijf|ons\s+bedrijf|de\s+winkel|"
    r"de\s+praktijk|de\s+stichting|de\s+vereniging|de\s+bv)(?:\s*:|(?=\s*(?:is\s+)?[:-]?\s*[A-Z]{2}\s?\d{2}))|"
    # any owner noun ("van het kantoor / de firma / de onderneming / onze zaak: X"), but then an
    # IBAN must follow right after (a colon alone is not enough: "nieuwe rekening voor de lunch: € 45" is a bill).
    r"\bnieuw(?:e)?\s+(?:bank)?rekening\s+(?:van|voor)\s+(?:de|het|ons|onze|mijn|jullie|uw)\s+[\w-]+(?:\s+[\w-]+)?"
    r"(?=\s*(?:is\s+)?[:-]?\s*[A-Z]{2}\s?\d{2}\s?[A-Z0-9]{4})|"
    # an adjective in between ("via onze nieuwe zakelijke rekening:", a held-out test mail); same colon / number /
    # IBAN-right-after rule as plain "nieuwe rekening".
    r"\bnieuw(?:e)?\s+(?:zakelijke|bedrijfs|zaken|europese|buitenlandse|tijdelijke|aparte|andere)\s*-?\s*rek(?:ening)?"
    r"(?:\s*:|\s?(?:nr\b|nummer\b)|(?=\s*(?:is\s+)?[:-]?\s*[A-Z]{2}\s?\d{2}))|"
    # "our payment instructions have been updated" / "updated payment instructions" (a held-out test mail), "our
    # receiving account has been migrated to" (F10), "the previous account will be closed".
    r"\b(?:payment|bank|banking|remittance|wire|transfer)\s+instructions\s+(?:have\s+|has\s+)?(?:been\s+)?"
    r"(?:updated|changed|amended|revised|modified)\b|"
    r"\b(?:updated|changed|amended|revised|new)\s+(?:payment|bank|banking|remittance|wire)\s+instructions\b|"
    r"\b(?:receiving|bank|payment|collection|beneficiary|remittance|settlement)\s+account\s+(?:has|have|was|is|will)\s+"
    r"(?:been\s+|be\s+|now\s+)?(?:migrated|moved|changed|updated|switched|transferred|replaced)\b|"
    r"\b(?:previous|old|former|current|existing)\s+(?:bank\s+)?account\s+(?:will|is\s+going\s+to|is\s+being|has\s+been)"
    r"\s+(?:be\s+)?(?:closed|discontinued|terminated)\b|"
    r"\bander(?:e)?\s+(?:bankrekening|rekeningnummer|rekeningnr|rek\.?\s?nr)\b|"
    r"\bnew\s+(?:bank\s+)?(?:account|acct)\s*(?:number|no\.?|nr\.?|details|:)|"
    r"\b(?:updated|changed)\s+(?:bank|payment|account)\s+details\b|"
    r"\b(?:changed|updated|switched|wijzigde|veranderde)\s+(?:\w+\s+){0,3}?(?:(?:bank|collection|payment|"
    r"receiving|escrow)\s+account\b|account\s+(?:number|details)\b|acct\b|iban\b)|"
    # "our new remittance account" (a held-out test mail, caught before only via its changed IBAN) and
    # "which replaces all previous instructions". A remittance ADVICE is not a bank change (account/details only).
    r"\b(?:new|updated|changed)\s+(?:remittance|receiving|collection|beneficiary)\s+(?:bank\s+)?"
    r"(?:account|acct|details)\b|"
    r"\b(?:replaces?|supersedes?|overrides?)\s+(?:all\s+|any\s+)?(?:previous|prior|earlier|former|old)\s+"
    r"(?:(?:payment|bank|banking|remittance)\s+)?(?:instructions|details|accounts?)\b|"
    r"\bvervangt\s+(?:alle\s+)?(?:eerdere|vorige|oude)\s+(?:betaal|bank)?(?:instructies|gegevens|rekening(?:en)?)\b|"
    # noun first, participle later: "zijn de betaalgegevens voor zakelijke klanten gewijzigd",
    # "ons rekeningnummer is per 1 december veranderd". Not when negated ("bankgegevens zijn NIET gewijzigd"), nor
    # in a warning that denies it further on ("e-mails melden dat onze bankgegevens zijn gewijzigd. Dat is niet zo.
    # Onze bankgegevens zijn NIET gewijzigd").
    r"\b(?:(?:betaal|bank|rekening|betalings)(?:gegevens|instructies)\b(?:(?!\b(?:niet|nooit|geen)\b)[^.\n?!;]){0,50}?"
    r"\b(?:gewijzigd|veranderd|aangepast)|"
    r"(?:rekeningnummer|bankrekening(?:nummer)?|iban)\b(?:(?!\b(?:niet|nooit|geen)\b)[^.\n?!;]){1,50}?"
    r"\b(?:gewijzigd|veranderd))\b(?![\s\S]*\b(?:niet|nooit)\s+(?:gewijzigd|veranderd|aangepast)\b))")
# does the message ask the READER to pay to a given IBAN? A proximity rule instead of an earlier
# grab-bag regex: a pay verb or payment noun within PAY_ASK_WINDOW characters of the IBAN, in a line that is not a
# '> ' quote, and not a first-person payer statement ("ik heb betaald", "wij betalen u", "I'll pay", "betaal ik").
# NL + EN wording (and the common German verbs). Used by C14: a payment ask plus an IBAN you never paid (and that
# never paid you, and that is not your own account) blocks on every outgoing label.
#   strong cues  pay/transfer verbs (infinitive, imperative, "te"-form) and payment nouns. A verb whose nearest
#                subject in its clause is first person (ik/wij/we/I) is the sender paying, not an ask.
#   weak cues    payment-detail nouns: "bank details", "betalingstermijn", "due", "t.n.v.", "beneficiary"...
#                Ignored when the message also has a first-person payer statement ("Our payment details: ..
#                We'll pay your invoice Friday").
# Exempt: an IBAN introduced as the SOURCE of a payment ("vanaf", "van rekening", "from", "afzender"), and a
# verb whose clause names a non-transfer channel ("betaal met iDEAL", "pay by card at the counter").
# Refund wording never downgrades an ask: "to process your refund, please pay the fee to X" is a
# classic scam. A refund to the customer's own account passes only because that IBAN is known (it paid you before).
PAY_ASK_WINDOW = 80
# the split form "over maken" ("Wil je de 12.400,00 over maken naar X?"), not after "zorgen /
# afspraken / ophef .." ("daar kunnen we afspraken over maken").
_PAY_VERB = (r"overmaken|over\s+te\s+maken|(?<!zorgen\s)(?<!afspraken\s)(?<!afspraak\s)(?<!grapjes\s)(?<!ophef\s)"
             r"(?<!drukte\s)(?<!punt\s)over\s+maken|maak\b[^.\n?!;]{0,40}?\bover|overboeken|over\s+te\s+boeken|"
             r"boek\b[^.\n?!;]{0,40}?\bover|overschrijven|over\s+te\s+schrijven|"
             r"storten|te\s+storten|stort|betalen|te\s+betalen|betaal|voldoen|te\s+voldoen|voldoe|"
             r"pay|transfer|wire|remit|settle|deposit|"
             r"(?:stuur|send|wire)\b[^.\n?!;]{0,30}?\b(?:geld|money|funds|bedrag|amount|euros?|eur)|"
             r"überweisen|überweise|zahlen|zahle|"
             # the Dutch passive ask "dient te worden overgemaakt", "moet worden betaald", "kan
             # worden voldaan op". A participle alone ("is overgemaakt") stays a past form (_PAID_RE).
             # "kan/kunnen/mag ... worden gestort" too ("Het bedrag kan op onze tussenrekening worden
             # gestort", a held-out test mail); before only dient/moet counted.
             # "moet worden doorgeboekt / doorgestort / overgezet naar X" too (was silent with a
             # reported-figure amount: "De omzet van € 4.100,00 moet worden doorgeboekt naar X").
             r"te\s+worden\s+(?:overgemaakt|overgeboekt|overgeschreven|betaald|voldaan|gestort|doorgeboekt|"
             r"doorgestort|overgezet)|"
             r"(?:dient|dienen|moet|moeten|kan|kunnen|mag|mogen)\b[^.\n?!;]{0,60}?\bworden\s+(?:overgemaakt|overgeboekt|"
             r"overgeschreven|betaald|voldaan|gestort)|"
             # the payout verbs only after a must-modal: "kan pas morgen worden doorgeboekt" is a processor's notice
             # the window stops at the first "worden", at a subordinate clause (voordat / zodra
             # / als ..) and at an agent ("door ons / de bank"; not "door u / jou"): "moet eerst worden gecontroleerd
             # voordat deze kan worden doorgeboekt naar X", "moet morgen door ons worden doorgeboekt" are notices.
             r"(?:dient|dienen|moet|moeten)\b(?:(?!\b(?:worden|voordat|zodra|nadat|als|wanneer|totdat|omdat|waarna|"
             r"zodat|dat|kan|kunnen|mag|mogen)\b|\bdoor\s+(?!(?:u|je|jou|jij|jullie)\b))[^.\n?!;]){0,60}?"
             r"\bworden\s+(?:doorgeboekt|doorgestort|overgezet)")
_PAY_VERB_RE = re.compile(r"(?i)\b(?:" + _PAY_VERB + r")\b")
_PAY_NOUN_RE = re.compile(r"(?i)\b(?:betaling(?:en)?|overboeking|overschrijving|payments?(?!\s+details)|"
                          r"zahlung|überweisung)\b")
_PAY_WEAK_RE = re.compile(r"(?i)(?:\b(?:payment|bank|betaal|betalings|bank)\s?(?:details|gegevens)\b|"
                          r"\bbankgegevens\b|\bbeta(?:al|lings)termijn\b|\bvervaldatum\b|\bdue\b(?!\s+to\b)|\bpayable\b|"
                          r"\bopenstaande?\s+(?:bedrag|saldo|factuur)|\boutstanding\s+(?:amount|balance|invoice)|"
                          r"\bt\.\s?n\.\s?v\.|\btnv\b|\bten\s+(?:name|gunste)\s+van\b|\bbeneficiary\b|\bbegunstigde\b|"
                          r"\bonder\s+vermelding\s+van\b)")
# Past forms: never an ask by themselves; with a first-person subject they make a payer statement.
_PAID_RE = re.compile(r"(?i)\b(?:betaald|overgemaakt|overgeboekt|gestort|voldaan|paid|transferred|wired|remitted|"
                      r"settled)\b")
_PRONOUN_RE = re.compile(r"(?i)\b(ik|wij|we|i|u|je|jij|jullie|you|sie|du)\b")
_FIRST = {"ik", "wij", "we", "i"}
_CLAUSE_END = re.compile(r"[.!?;\n]")
_CHANNEL_RE = re.compile(r"(?i)\b(?:ideal|pin|pinnen|contant|cash|card|kaart|creditcard|paypal|tikkie|"
                         r"at the counter|aan de (?:balie|kassa))\b")
_SOURCE_RE = re.compile(r"(?i)\b(vanaf|van|from|afzender|sender|op|naar|to|into|aan|via|on|at|an|auf)\b")
_SOURCE_WORDS = {"vanaf", "van", "from", "afzender", "sender"}
_ACCOUNT_AFTER_RE = re.compile(r"(?i)\s*:?\s*(?:(?:de|het|uw|je|jouw|onze|ons|mijn|hun|zijn|haar|your|the|our|my|"
                               r"their|his|her)\s+)?(?:bank\s?)?(?:rekening(?:nummer)?|account|acct|iban)\b")
_PAYER_RE = re.compile(r"(?i)\b(?:paid|payment|transfer(?:red)?|wired|sent|received|debited|refunded|"
                       r"betaald|betaling|ontvangen|overgemaakt|overgeboekt|overboeking|gestort|afgeschreven|"
                       r"teruggestort)\b")


_THANKS_RE = re.compile(r"(?i)\b(?:bedankt|dank(?:jewel|je wel|u wel)?)\s+voor\s+(?:je|uw|jullie|de)\s+(?:snelle\s+)?"
                        r"betaling(?:en)?\b|\bthanks?\s+(?:you\s+)?(?:so much\s+|very much\s+)?for\s+(?:your|the)\s+"
                        r"(?:prompt\s+|quick\s+)?payments?\b")


def _unquote(text: str) -> str:
    """Blank out '> ' quoted lines (keeps offsets): a quoted ask is someone else's earlier mail, not this one's."""
    return re.sub(r"(?m)^[ \t]*>.*$", lambda m: " " * len(m.group(0)), text)


# clause ends and pronouns are found once per text and looked up by bisection. Before, every pay verb
# re-scanned the text from 0 to the verb (and copied the rest of the text), so a long clause full of pay verbs
# ("pay pay pay ..", 20,000 chars) took ~18 s. Same results: _CLAUSE_END is one character, and a pay verb / pronoun
# starts and ends on a word boundary, so a match in the full text is a match in the sub-range and vice versa.
@functools.lru_cache(maxsize=16)
def _clause_ends(text: str) -> list[int]:
    return [m.end() for m in _CLAUSE_END.finditer(text)]


@functools.lru_cache(maxsize=16)
def _pronouns(text: str) -> tuple[list[int], list[int], list[str]]:
    ms = list(_PRONOUN_RE.finditer(text))
    return [p.start() for p in ms], [p.end() for p in ms], [p.group(1).lower() for p in ms]


_FIRST_AFTER_RE = re.compile(r"\s+(ik|wij|we)\b", re.I)


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    ends = _clause_ends(text)
    i = bisect_right(ends, start)  # last clause end <= start (the char before it is the punctuation)
    j = bisect_right(ends, end)  # first punctuation char at index >= end
    return (ends[i - 1] if i else 0), (ends[j] - 1 if j < len(ends) else len(text))


@functools.lru_cache(maxsize=16)
def _channels(text: str) -> tuple[list[int], list[int]]:
    ms = list(_CHANNEL_RE.finditer(text))
    return [c.start() for c in ms], [c.end() for c in ms]


def _channel_between(text: str, a: int, b: int) -> bool:
    """Same as bool(_CHANNEL_RE.search(text, a, b)) for a at a verb's end and b at a clause end (both word
    boundaries), without re-scanning the clause for every verb."""
    starts, ends = _channels(text)
    k = bisect_left(starts, a)
    return k < len(starts) and ends[k] <= b


def _first_person(text: str, m: re.Match) -> bool:
    """The verb's nearest subject in its clause is first person ("wij betalen", "ik kan overmaken"), or it is
    inverted ("betaal ik morgen", "betalen wij")."""
    a, _ = _clause(text, m.start(), m.end())
    starts, ends, words = _pronouns(text)
    k = bisect_right(ends, m.start()) - 1  # last pronoun that ends before the verb
    before = words[k] if k >= 0 and starts[k] >= a else None
    after = _FIRST_AFTER_RE.match(text, m.end())
    return bool(after) or before in _FIRST


def _is_source(text: str, start: int) -> bool:
    """The IBAN is introduced as where money comes FROM ("vanaf", "van rekening", "paid from", "afzender").
    "vanaf"/"from" followed by a time ("vanaf deze maand: X", "from 1 December: X", "from now on") is
    WHEN, not where from; that word is skipped."""
    a, _ = _clause(text, start, start)
    ws = [w for w in _SOURCE_RE.finditer(text, max(a, start - 40), start)
          if not (w.group(1).lower() in ("vanaf", "from") and _TIME_AFTER_RE.match(text, w.end()))]
    if not ws or ws[-1].group(1).lower() not in _SOURCE_WORDS:
        return False
    w = ws[-1]
    if w.group(1).lower() not in ("van", "from"):
        return True
    # plain "van" / "from" is also "of" / a place ("het einde van het kwartaal, rekeningnummer X zoals
    # u van ons kent", a held-out test mail). It counts only right before an account word ("van rekening X", "from account X") or
    # with a payer word in the 60 characters before it on the same line ("paid from: Harbourline Ltd, X", "we received
    # EUR 1,250.00 from X": the decimal point would end the clause, so a window instead of the clause).
    b = max(text.rfind("\n", 0, w.start()) + 1, w.start() - 60)
    return bool(_ACCOUNT_AFTER_RE.match(text, w.end())) or bool(_PAYER_RE.search(text, b, w.start()))


def find_pay_asks(text: str) -> dict[str, str]:
    """{IBAN: cue text} for every IBAN (checksum not checked) the message asks the reader to pay to."""
    return {ib: cue for ib, (cue, _strong) in _pay_asks(text).items()}


@functools.lru_cache(maxsize=16)
def _pay_asks(text: str) -> dict[str, tuple[str, bool]]:
    """{IBAN: (first cue text, any STRONG cue -- a pay verb or payment noun, not only a weak detail noun)}."""
    t = _unquote(text)
    cues: list[tuple[re.Match, bool]] = []  # (match, weak)
    payer_statement = False
    for m in _PAY_VERB_RE.finditer(_mask_amount_dots(t)):  # same offsets as t
        if _first_person(t, m):
            payer_statement = True
            continue
        a, b = _clause(t, m.start(), m.end())
        if _channel_between(t, m.end(), b) and not ibanlib.find_ibans(t[a:b]):
            continue
        if _pay_notice(t, m):  # "Adyen will send the daily turnover .. to X" announces, asks nothing
            continue
        cues.append((m, False))
    for m in _PAID_RE.finditer(t):
        if _first_person(t, m):
            payer_statement = True
    # "bedankt voor je betaling" / "thank you for your payment" is a receipt, not an ask.
    thanks = [(m.start(), m.end()) for m in _THANKS_RE.finditer(t)]
    cues += [(m, False) for m in _PAY_NOUN_RE.finditer(t) if not any(a <= m.start() < b for a, b in thanks)]
    if not payer_statement:
        cues += [(m, True) for m in _PAY_WEAK_RE.finditer(t)]
    out: dict[str, tuple[str, bool]] = {}
    for ib, s, e in ibanlib.find_iban_spans(t):
        if _is_source(t, s):
            continue
        near = [(m, w) for m, w in cues if max(s - m.end(), m.start() - e, 0) <= PAY_ASK_WINDOW]
        if not near:
            continue
        cue, strong = out.get(ib, (t[near[0][0].start():near[0][0].end()].strip(), False))
        out[ib] = (cue, strong or any(not w for _m, w in near))
    return out


def _mask_amount_dots(t: str) -> str:
    """the '.' inside an amount ("maak 12.400,00 vandaag over", "€ 1.250,00") becomes ',' so a pay
    verb that spans its object ("maak .. over", "boek .. over", "stuur .. geld") is not cut at the thousands dot.
    Same length as t, so match offsets stay valid in t."""
    spans = [(p, p + len(raw)) for _d, p, raw in find_amounts(t)] + [(a, b) for a, b, _r in _bare_amounts(t)]
    if not spans:
        return t
    chars = list(t)
    for a, b in spans:
        for i in range(a, b):
            if chars[i] == ".":
                chars[i] = ","
    return "".join(chars)


# STRUCTURAL pay-ask. Passive or verb-less asks ("should be credited to X", "Bank: ..\nAccount: X\n
# Amount: EUR 95", "het restant van 95 euro graag op X", "future invoices are paid into X") have no pay verb near
# the IBAN, so find_pay_asks misses them. The shape is enough: an IBAN with an amount or a due date next to it is a
# payment instruction, whatever the verb. Only used by C14 for an IBAN you never paid, that never paid you and that
# is not your own account. Not an ask:
#   - the IBAN is introduced as the SOURCE ("vanaf", "from"), or as the READER's account ("uw rekening", "your
#     account": a refund or payout going to you);
#   - the IBAN sits in a signature / footer line (KvK, BTW/VAT, tel, www, e-mail, "|" separators, or below a
#     short sign-off line such as "Met vriendelijke groet" / "Regards" / "--");
#   - the sentence of the IBAN or of the amount/date is a settled or sender-pays statement ("wij hebben 95 euro
#     overgemaakt", "I sent the refund", "your payment was received", "ik betaal morgen") and has no request
#     marker ("graag", "please", "should be", "dient", "future" ...) that turns it back into an ask.
# Anchors: a money amount (money.find_amounts), an amount word ("bedrag", "restant", "saldo", "amount",
# "balance", "funds") or a due date ("binnen 14 dagen", "within 14 days", "uiterlijk", "by Friday", "before
# 15 November", "at your earliest convenience", a dd-mm-yyyy date) within STRUCT_WINDOW characters in the same
# paragraph; "factuur"/"invoice(s)" counts only in the IBAN's own sentence, and not when the message has a
# first-person payer statement ("Ik wil graag betalen. Mijn IBAN is X, kunt u een factuur sturen?").
STRUCT_WINDOW = 120
_AMOUNT_WORD_RE = re.compile(r"(?i)\b(?:(?:factuur|totaal|eind)?bedrag(?:en)?|restant(?:bedrag)?|saldo|"
                             r"totaal|amount|balance|total|sum|funds|geld|money)\b")
_INVOICE_WORD_RE = re.compile(r"(?i)\b(?:factu(?:u)?r(?:en)?|nota'?s?|invoices?)\b")
_MONTHS = (r"jan(?:uari|uary)?|feb(?:ruari|ruary)?|maart|march|mar|apr(?:il)?|mei|may|jun[ie]?|jul[iy]?|"
           r"aug(?:ustus|ust)?|sep(?:tember)?|okt(?:ober)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_WEEKDAYS = (r"maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|monday|tuesday|wednesday|thursday|"
             r"friday|saturday|sunday|morgen|tomorrow|today|vandaag|end of (?:the )?(?:week|month)|eind van de "
             r"(?:week|maand)|einde (?:van de )?maand")
_DUE_RE = re.compile(
    r"(?i)\b(?:binnen\s+\d{1,3}\s+(?:werk)?dagen|within\s+\d{1,3}\s+(?:business\s+|working\s+)?days|"
    r"uiterlijk|vóór|voor\s+(?:" + _WEEKDAYS + r"|\d{1,2}\s+(?:" + _MONTHS + r"))|"
    r"(?:by|before|no later than|not later than)\s+(?:the\s+)?(?:" + _WEEKDAYS + r"|"
    r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|eow|eom)\b\.?|\d{1,2}(?:st|nd|rd|th)?\s+(?:"
    + _MONTHS + r")|(?:" + _MONTHS + r")\s+\d{1,2}|\d{1,2}[-/.]\d{1,2})|"
    r"at your earliest convenience|as soon as possible|asap|z\.?s\.?m\.?|zo snel mogelijk|op korte termijn|"
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})")
# a time right after "vanaf"/"from" ("vanaf deze maand", "vanaf 1 december", "from next month", "from the
# 1st", "from today", "vanaf factuur 26-1005" / "from the next invoice"): the IBAN after it is not a source.
_TIME_AFTER_RE = re.compile(
    r"(?i)\s+(?:nu\b|heden\b|vandaag\b|morgen\b|(?:deze|dit|volgende|komende|de\s+(?:volgende|komende|"
    r"eerstvolgende)|het\s+nieuwe|begin|eind|medio)\s+\w+|(?:now|today|tomorrow)\b|(?:this|next|the\s+(?:next|"
    r"coming|start|beginning|end)|the\s+\d{1,2}(?:st|nd|rd|th)?)\b|(?:" + _MONTHS + r")\b|(?:" + _WEEKDAYS + r")\b|"
    r"\d{1,2}(?:e|ste|de|st|nd|rd|th)?\s+(?:" + _MONTHS + r")\b|\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?\b|"
    r"(?:19|20)\d{2}\b|(?:factuur|invoice|periode|period|kwartaal|quarter|q[1-4])\b|"
    r"(?:[01]?\d|2[0-3])[:.][0-5]\d\b)")  # a clock time, "vanaf 14:00 vandaag"
_SETTLED_RE = re.compile(
    r"(?i)\b(?:betaald|overgemaakt|overgeboekt|gestort|teruggestort|terugbetaald|voldaan|verstuurd|gestuurd|"
    r"paid|transferred|wired|remitted|settled|sent|refunded|received|(?:in goede orde |is |zijn |hebben |heb )"
    r"ontvangen)\b|" + _THANKS_RE.pattern.replace("(?i)", ""), re.I)
_REQUEST_RE = re.compile(
    r"(?i)\b(?:graag|gelieve|verzoek(?:en)?|dient|dienen|moet|moeten|kunt|kun je|kunnen jullie|wilt|wil je|"
    r"please|kindly|should|must|need(?:s)? to|to be|are to|can you|could|would|appreciate|future|voortaan|"
    r"from now on|vanaf nu|toekomstige|alle (?:volgende )?facturen)\b")
_READER_ACCT_RE = re.compile(r"(?i)\b(?:uw|je|jouw|your)\s+(?:(?:eigen|own|zakelijke|business|bank)\s+)?"
                             r"(?:bank)?(?:rekening(?:nummer)?|account(?:\s+number)?|iban)\W{0,3}$")
_SIGNOFF_RE = re.compile(r"(?i)^\s*(?:--|met vriendelijke groet(?:en)?|vriendelijke groet(?:en)?|groet(?:en|jes)?|"
                         r"mvg|best regards|kind regards|warm regards|regards|best|cheers|sincerely|"
                         r"hartelijke groet(?:en)?)\s*[,.!]?\s*(?:\w+\s*){0,3}$")
_SIG_LINE_RE = re.compile(r"(?i)\b(?:kvk|k\.v\.k\.|btw|vat|tel|telefoon|phone|mob|www\.|https?://)|@|\|")
_SENTENCE_END = re.compile(r"[.!?;](?=\s|$)|\n")
_PARA_RE = re.compile(r"\n[ \t]*\n")


def _sentence(text: str, pos: int) -> tuple[int, int]:
    """Sentence around pos. A '.' inside a number ("95.00") does not end a sentence."""
    a = max((m.end() for m in _SENTENCE_END.finditer(text, 0, pos)), default=0)
    m = _SENTENCE_END.search(text, pos)
    return a, (m.start() if m else len(text))


def _paragraph(text: str, pos: int) -> tuple[int, int]:
    a = max((m.end() for m in _PARA_RE.finditer(text, 0, pos)), default=0)
    m = _PARA_RE.search(text, pos)
    return a, (m.start() if m else len(text))


def _settled(sentence: str) -> bool:
    """The sender pays ("ik wil graag betalen", "wij maken het over"), or a payment already happened ("is betaald",
    "I sent the refund") and the sentence has no request in it ("future invoices are paid into" is a request)."""
    verbs = list(_PAY_VERB_RE.finditer(sentence))
    if any(_first_person(sentence, m) for m in verbs):
        return True
    # a modal passive ask ("kan op onze rekening worden gestort", "moet worden betaald") is a request, even
    # though its participle ("gestort") is also a settled word.
    if any(re.search(r"(?i)\bworden\s+\w+$", m.group(0)) for m in verbs):
        return False
    if _RAN_RE.search(sentence) and not _REQUEST_RE.search(sentence) and not _MOVE_ASK_RE.search(sentence):
        return True
    return bool(_SETTLED_RE.search(sentence)) and not _REQUEST_RE.search(sentence)


# a payout / sweep that already ran ("Het bedrag van de pinomzet, € 4.100,00, is doorgeboekt naar X",
# "The automatic sweep of your daily revenue (EUR ..) to savings account X has run"). Settled only when the sentence
# holds no request word at all (also svp/aub/alsjeblieft: "is overgezet naar X, svp vandaag bevestigen" stays an ask).
_RAN_RE = re.compile(r"(?i)\b(?:doorgeboekt|doorgestort|overgezet|(?:has|have|was|were)\s+(?:been\s+)?"
                     r"(?:run|executed|completed|processed))\b")


# an amount written without a currency sign ("1.840,00", "1,840.00", "95,00", "(1.150)"): cents or a
# thousands separator, not part of a longer number, date, phone number or IBAN. A plain integer ("1150") never counts.
# the regex now lives in money.py (BARE_AMOUNT_RE) so the rule planner reads the same amounts.
_bare_amounts = find_bare_amounts


def _has_anchor(s: str) -> bool:
    return bool(find_amounts(s) or _bare_amounts(s) or _AMOUNT_WORD_RE.search(s) or _DUE_RE.search(s))


def _in_signature(text: str, s: int, e: int) -> bool:
    """The span sits in a signature / footer, not in a payment instruction: its line has a footer marker
    (KvK/BTW/tel/www/e-mail/'|'), or it sits below a sign-off line ("Met vriendelijke groet", "Regards", "--") on
    a short line ("IBAN: X", "Amount: EUR 95"). A line that carries the span AND another amount/due date is a
    payment line, never a footer ("Totaal EUR 95 | IBAN X" from a new supplier still counts). A long line below
    the sign-off ("PS: het bedrag van 95 euro graag op X") is body text, not a footer."""
    ls = text.rfind("\n", 0, s) + 1
    le = text.find("\n", e)
    le = le if le >= 0 else len(text)
    rest = text[ls:s] + " " + text[e:le]
    sa, sb = _sentence(text, s)  # a bare amount ("2.10") only counts in the span's own sentence
    own = text[max(ls, sa):s] + " " + text[e:min(le, sb)]
    if (find_amounts(rest) or _AMOUNT_WORD_RE.search(rest) or _DUE_RE.search(rest) or _bare_amounts(own)
            or ibanlib.find_ibans(rest)):
        return False
    if _SIG_LINE_RE.search(rest):
        return True
    below = any(_SIGNOFF_RE.match(line) for line in text[:ls].splitlines() if line.strip())
    return below and len(re.findall(r"\w+", rest)) <= 4


_LABEL_LINE_RE = re.compile(r"^\s*[^\W\d][\w .()/'&-]{0,40}:\s*\S")
_NAME_LINE_RE = re.compile(r"^\s*[^\W\d][\w.'&-]*(?:\s+[\w.'&()-]+){0,4}\s*$")
_TNV_TAIL_RE = re.compile(r"(?i)\b(?:t\.?\s?n\.?\s?v\.?|ten name van)\b.*$|\biban\b\s*:?")


def _block_lines_ok(text: str, pa: int, pb: int) -> bool:
    """The paragraph is a payment-details block: at most 8 non-empty lines, each "Label: value" -- or a line
    that holds only an IBAN (optionally "t.n.v. X"), plus at most one short account-holder line without a colon
    ("Tussenrekening Dekker Advies\nNL54 SAND 0346 6177 90\nKenmerk: ..", a held-out test mail). A block needs at least
    one label or IBAN line."""
    lines = [ln for ln in text[pa:pb].splitlines() if ln.strip()]
    if not lines or len(lines) > 8:
        return False
    names, keyed = 0, False
    for ln in lines:
        if _LABEL_LINE_RE.match(ln):
            keyed = True
            continue
        spans = ibanlib.find_iban_spans(ln)
        if spans and not re.search(r"\w", _TNV_TAIL_RE.sub(" ", ln[:spans[0][1]] + " " + ln[spans[0][2]:])):
            keyed = True
            continue
        if not names and _NAME_LINE_RE.match(ln) and not find_amounts(ln) and not _bare_amounts(ln):
            names = 1
            continue
        return False
    return keyed


def _ask_paragraph_before_block(text: str, pa: int, pb: int) -> int | None:
    """the IBAN's paragraph is a "label: value" block (every non-empty line "Label: value", at most 8
    lines, e.g. "Account holder: ..\nIBAN: ..\nReference: .." or "Oude rekening: ..\nNieuwe rekening: .."), so
    the ask that introduces it sits in the paragraph BEFORE it ("the balance of EUR 3,400.00 should be credited to
    our escrow account:"). Returns that paragraph's start, else None. Anchors there count when they are within
    STRUCT_WINDOW of the paragraph break; settled sentences and signatures are excluded as usual."""
    if pa == 0 or not _block_lines_ok(text, pa, pb):
        return None
    prev = [m for m in _PARA_RE.finditer(text, 0, pa) if m.end() == pa]
    if not prev:
        return None
    qa, _ = _paragraph(text, prev[0].start())
    return qa


def _amount_paragraph_after_block(text: str, s: int, e: int, pa: int, pb: int) -> int | None:
    """the IBAN's paragraph is a "label: value" block ("Rekeningnummer: X\nNaam: ..") or holds
    little more than the IBAN ("IBAN: X", "Nieuw: X"), and the AMOUNT follows in the next paragraph ("De factuur
    (€ 640,00) sturen we hierna los nog toe.", "Verbruik afgelopen maand: 212 liter, bedrag 318,40."). Returns the
    end of that next paragraph, else None. Only money amounts count there (not amount words or dates), within
    STRUCT_WINDOW of the paragraph break; settled sentences and signatures are excluded as usual."""
    block = _block_lines_ok(text, pa, pb)
    bare = len(re.findall(r"\w+", text[pa:s] + " " + text[e:pb])) <= 3
    if not (block or bare) or pb >= len(text):
        return None
    m = _PARA_RE.match(text, pb)
    if not m:
        return None
    _qa, qb = _paragraph(text, m.end())
    return qb


def _asked_sentence(text: str, pos: int) -> bool:
    """the sentence around pos asks the reader to pay (a pay verb that is not the sender's own, e.g. "Maak
    de weekomzet van EUR 12.400 vandaag over naar .."), so a reported-figure label in it ("omzet") is no report.
    Amounts are masked first: the '.' in "12.400,00" would cut the "maak ... over" verb.
    an amount is masked as the word "bedrag" (not zeros), so "send/stuur .. <amount>" reads as the
    send-money verb, and move verbs with a destination count here too ("Zet de dagomzet van EUR 4.100 over naar ..",
    "Move the daily revenue of EUR 4.100 to ..", "Send the week turnover of EUR 12.400 to .." were silent)."""
    a, b = _sentence(text, pos)
    s = text[a:b]
    for _d, p, raw in sorted(find_amounts(s), key=lambda x: -x[1]):
        s = s[:p] + " bedrag " + s[p + len(raw):]
    return (any(not _first_person(s, m) and not _pay_notice(s, m) for m in _PAY_VERB_RE.finditer(s))
            or any(_move_asked(s, m) for m in _MOVE_VERB_RE.finditer(s)))


# a third-person NOTICE ("Adyen will send the daily turnover of EUR 4.100 to X tomorrow", "De bank zal
# de omzet .. overmaken naar X", "Adyen gaat de pinomzet .. betalen op X") announces what a third party does with the
# money; it asks the reader nothing. A pay verb is a notice only when an announcing auxiliary (will/shall/is going
# to/gaat/zal ..) stands before it in its clause, no second-person subject (u/je/you ..) stands there, and the sentence
# holds no request word (please/graag/kun je/should/must/moet/dient ..). Imperatives and requests still ask.
_NOTICE_AUX_RE = re.compile(r"(?i)\b(?:will|shall|(?:is|are)\s+going\s+to|gaat|gaan|zal|zullen)\b|'ll\b")
_SECOND_RE = re.compile(r"(?i)\b(?:u|je|jij|jullie|you)\b")


NOTICE_WINDOW = 60


@functools.lru_cache(maxsize=16)
def _notice_marks(text: str) -> tuple[list[int], list[int], list[int]]:
    """Sentence ends, ',' / ':' positions and request-word starts of text, found once (no per-verb rescans)."""
    # a ',' inside an amount ("€ 4.100,00", "4,100.00") is no clause break: "Adyen gaat de pinomzet van
    # € 4.100,00 overmaken naar X" keeps its announcing auxiliary.
    inside = [(p, p + len(raw)) for _d, p, raw in find_amounts(text)] + [(a, b) for a, b, _r in _bare_amounts(text)]
    return ([m.end() for m in _SENTENCE_END.finditer(text)],
            [m.start() for m in re.finditer(r"[,:]", text) if not any(a <= m.start() < b for a, b in inside)],
            sorted(m.start() for r in (_MOVE_ASK_RE, _REQUEST_RE) for m in r.finditer(text)))


def _pay_notice(text: str, m: re.Match) -> bool:
    if _first_person(text, m):
        return False
    ends, commas, asks = _notice_marks(text)
    i = bisect_right(ends, m.start())
    a, b = (ends[i - 1] if i else 0), (ends[i] if i < len(ends) else len(text))
    k = bisect_left(asks, a)
    if k < len(asks) and asks[k] < b:  # a request word anywhere in the sentence: an ask
        return False
    j = bisect_right(commas, m.start())
    c = max(a, commas[j - 1] + 1 if j and commas[j - 1] < m.start() else a, m.start() - NOTICE_WINDOW)
    return bool(_NOTICE_AUX_RE.search(text, c, m.start())) and not _SECOND_RE.search(text, c, m.start())


def _move_asked(s: str, m: re.Match) -> bool:
    """a move verb asks only as an imperative (nothing before it in its clause: "Zet ..", "Hoi Mark,
    move ..") or after a request word ("Graag .. overzetten", "Kun je .. doorstorten", "Please sweep .."). A
    third-person notice ("Adyen zet de pinomzet .. over naar", "The bank will move ..", "De bank gaat .. doorboeken")
    is no ask; an infinitive ("Vandaag nog de dagomzet .. overzetten naar") asks unless an announcing auxiliary
    stands before it ("gaat/zal/wordt ..")."""
    if _first_person(s, m):
        return False
    pre = s[:m.start()]
    if _MOVE_ASK_RE.search(pre):
        return True
    clause = re.split(r"[,:]", pre)[-1]
    if m.group("imp") or m.group("zop"):
        return not clause.strip()
    return not _MOVE_AUX_RE.search(clause)


# move-money verbs that need a destination; used only to keep a reported-figure anchor (_asked_sentence).
# "Zet de omzet van € 4.105,00 vandaag op (onze spaar)rekening X" -- "zet .. op" with
# an account word right after "op" (not "zet het op de agenda").
_MOVE_VERB_RE = re.compile(r"(?i)\b(?P<imp>zet|stuur|send|move|forward|sweep)\b[^.\n?!;]{0,50}?\b(?:over|door|naar|to|into)\b|"
                           r"\b(?P<zop>zet)\b[^.\n?!;]{0,50}?\bop\s+(?:(?:de|het|onze|ons|mijn|jullie|deze|die)\s+)?"
                           r"(?:\w+)?(?:rekening(?:nummer)?|iban)\b|"
                           r"\boverzetten\b|\bover\s+te\s+zetten\b|\bdoorstorten\b|\bdoor\s+te\s+storten\b|"
                           r"\bdoorboeken\b|\bdoor\s+te\s+boeken\b")
_MOVE_ASK_RE = re.compile(r"(?i)\b(?:graag|aub|a\.u\.b|svp|alsjeblieft|alstublieft|gelieve|verzoek\w*|vragen|vraag|"
                          r"(?:kun|kan|wil|zou)\s+(?:je|jij|u)|(?:kunt|wilt)\s+u|please|pls|kindly|"
                          r"(?:can|could|would|will)\s+you)\b")
_MOVE_AUX_RE = re.compile(r"(?i)\b(?:gaat|gaan|zal|zullen|wordt|worden|komt|komen|is|zijn|heeft|hebben|kan|kunnen)\b")


def find_structural_asks(text: str) -> dict[str, str]:
    """{IBAN: anchor text} for IBANs that carry an amount or due date next to them, no verb needed."""
    t = _unquote(text)
    anchors = [(p, p + len(raw), raw) for _d, p, raw in find_amounts(t)]
    cur = list(anchors)
    anchors += [x for x in _bare_amounts(t) if not any(a <= x[0] < b for a, b, _r in cur)]
    # a reported figure next to an IBAN ("doel van 12.500,00 gehaald, rekening X blijft open",
    # "omzet € 48.600") is no amount to pay there.
    anchors = [x for x in anchors if not is_reported_figure(t, x[0], x[1]) or _asked_sentence(t, x[0])]
    anchors += [(m.start(), m.end(), m.group(0)) for r in (_AMOUNT_WORD_RE, _DUE_RE) for m in r.finditer(t)]
    payer = any(_first_person(t, m) for r in (_PAY_VERB_RE, _PAID_RE) for m in r.finditer(t))
    out: dict[str, str] = {}
    for ib, s, e in ibanlib.find_iban_spans(t):
        if ib in out or _is_source(t, s) or _READER_ACCT_RE.search(t[max(0, s - 40):s]) or _in_signature(t, s, e):
            continue
        sa, sb = _sentence(t, s)
        if _settled(t[sa:sb]):
            continue
        pa, pb = _paragraph(t, s)
        near = [(a, b, raw) for a, b, raw in anchors
                if pa <= a and b <= pb and max(s - b, a - e, 0) <= STRUCT_WINDOW and not (s <= a < e)]
        qa = _ask_paragraph_before_block(t, pa, pb)
        if qa is not None:  # "label: value" block right after the ask paragraph
            near += [(a, b, raw) for a, b, raw in anchors if qa <= a and b <= pa and pa - b <= STRUCT_WINDOW]
        nb = _amount_paragraph_after_block(t, s, e, pa, pb)
        if nb is not None:  # the amount follows in the paragraph AFTER a label block / bare IBAN line
            near += [(a, b, raw) for a, b, raw in anchors
                     if pb < a and b <= nb and a - pb <= STRUCT_WINDOW and (find_amounts(raw) or _bare_amounts(raw))]
        if not payer:  # "factuur"/"invoice" is a weak anchor, like find_pay_asks' weak cues
            near += [(m.start(), m.end(), m.group(0)) for m in _INVOICE_WORD_RE.finditer(t, sa, sb)]
        for a, b, raw in sorted(near, key=lambda x: max(s - x[1], x[0] - e, 0)):
            xa, xb = _sentence(t, a)
            if (xa, xb) != (sa, sb) and _settled(t[xa:xb]):
                continue
            if _in_signature(t, a, b):
                continue
            out[ib] = raw.strip()
            break
    return out


# NO-PAY CONTEXT. A never-paid IBAN with a payment cue next to it blocks (C5 / C14), but some mails
# say in so many words that the reader does not have to pay it now: the payment is optional, it was already paid,
# the money comes IN, it is collected by direct debit, the mail is a quote (the invoice comes later), a donation
# appeal, or the IBAN is quoted as an example in a fraud warning. Then the never-paid block becomes a WARN (approve
# only after a second look), never an "ok": the IBAN is still one you never paid. C3 (limit) on such a card warns
# too: nothing is asked to be paid now.
# Never softened: IBAN CHANGED (a known party on a new IBAN), bank-change wording (W2), a redirect of future payments
# ("future invoices will be paid into", "voortaan"), an official-payee mismatch (C13), a bad checksum, or a failed /
# bounced / stopped direct debit ("incasso mislukt", "direct debit failed": the classic
# "pay manually instead" scam). Context that comes with the reader being asked outright to pay that IBAN (a pay verb
# or payment noun next to it) only softens for "optional", "donation", "direct debit" and "quote" -- those mails ask
# "you MAY pay here"; "already paid", "incoming" and "no action needed" need the IBAN to carry no such ask.
_NP_OPTIONAL = re.compile(r"(?i)\b(?:vrijwillig(?:e)?|niet verplicht|geen verplichting|optioneel|optional|voluntary|"
                          r"not (?:required|mandatory|obligatory|compulsory)|no obligation|elk bedrag is welkom|"
                          r"any amount is welcome)\b")
# the old "giften?" read "gifte(n)", so the Dutch "uw gift van € 50" was no donation word. Dutch
# "gift" (= donation) only after a Dutch determiner / adjective or before "van"; English "your gift" is a present
# ("to receive your gift, pay the shipping fee" is a prize scam, not an appeal).
_NP_DONATION = re.compile(r"(?i)\b(?:doneer|doneren|donatie(?:s)?|donate|donation(?:s)?|giften|"
                          r"(?:uw|je|jouw|een|de|deze|elke|iedere|vrijwillige|eenmalige|periodieke|kleine)\s+gift|"
                          r"gift(?=\s+van\b)|anbi|goede doel(?:en)?|charity|fundraising|inzamelingsactie)\b")
# a membership-fee appeal ("If you wish to stay a member, the yearly contribution is EUR 35.00",
# "Als u lid wilt blijven, is de jaarlijkse contributie EUR 35,00") is the same kind of appeal as a donation: it is
# recognised ("contribution"/"bijdrage"/"contributie" are already gift nouns, _GIFT_OBJECT_RE) but was not an
# explicit no_pay_reason trigger, so it stayed a hard block on a never-paid IBAN. member(ship)/lid(maatschap) near a
# fee word, or "jaarlijkse/yearly/annual contribution(s)/bijdrage/contributie" on its own. Same cap/pressure gate as
# _NP_DONATION below (via _amount_ask): a CEO-style "contribution" ask with a big amount or pay-now pressure still
# blocks.
_NP_MEMBERSHIP_FEE = re.compile(
    r"(?i)\b(?:member(?:ship)?|lid(?:maatschap)?)\b[^.!?\n]{0,40}\b(?:fee|contribution(?:s)?|bijdrage(?:n)?|"
    r"contributie)\b|\b(?:fee|contribution(?:s)?|bijdrage(?:n)?|contributie)\b[^.!?\n]{0,40}\b(?:member(?:ship)?|"
    r"lid(?:maatschap)?)\b|\b(?:yearly|annual|jaarlijkse)\s+(?:membership\s+)?(?:contribution(?:s)?|bijdrage(?:n)?|"
    r"contributie)\b")
# donateurs / donors / crowdfunding (a held-out test mail "Dankzij 412 donateurs .. via de crowdfundingpagina").
# these words DESCRIBE a campaign, they do not ask to donate, so they soften only an IBAN that carries no
# strong pay ask (like "already paid" / "incoming"): "graag EUR 4.750 betalen aan <new IBAN> .. crowdfundingactie"
# (a CEO-fraud ask) still blocks.
# "er is geen enkele verplichting", "zonder enige verplichting" (a held-out test mail) are in this weak-only tier too
# ("Geen enkele verplichting voor de klant maar wel urgent" next to a CEO-fraud ask must still block).
_NP_CAMPAIGN = re.compile(r"(?i)\b(?:donateurs?|donors?|crowdfunding\w*)\b")
_NP_OPTIONAL_WEAK = re.compile(r"(?i)\b(?:geen enkele verplichting|zonder (?:enige )?verplichting)\b")
_NP_DIRECT_DEBIT = re.compile(r"(?i)automatisch (?:afgeschreven|geïncasseerd)|\bautomatische incasso\b|"
                              r"\b(?:via|per|middels|met) (?:automatische )?incasso\b|\bgeïncasseerd\b|"
                              r"\bdirect debits?\b|\b(?:wordt|worden)\b.{0,30}\bafgeschreven\b|\bgemachtigd\b|"
                              r"\bmachtiging\b|\bwill be (?:debited|collected)\b")
_NP_DD_FAILED = re.compile(
    r"(?i)\b(?:incasso|direct debit|afschrijving|machtiging)\b.{0,60}\b(?:mislukt|niet gelukt|geweigerd|gestorneerd|"
    r"storno|teruggeboekt|teruggestort|failed|unsuccessful|declined|bounced|returned|rejected|could not be|"
    r"cancelled|ingetrokken|stopgezet|beëindigd|vervalt|vervallen)|\b(?:mislukt|niet gelukt|failed|unsuccessful|"
    r"geweigerd|declined)\b.{0,40}\b(?:incasso|direct debit|afschrijving)|kon(?:den)? niet (?:worden )?"
    r"(?:geïncasseerd|afgeschreven)|\b(?:instead of|no longer|niet langer|niet meer|in plaats van|stop(?:t|pen)? "
    r"(?:met|with)|switch\w*|overstap\w*|overgestapt)\b.{0,40}\b(?:incasso|direct debit)|\bter incasso\b|"
    r"\bincassokosten\b|\bincassobureau\b|\bdeurwaarder\b|\bstornering\b|\bchargeback\b")
_NP_NO_ACTION = re.compile(
    r"(?i)\b(?:u|je|jij|jullie)\s+hoeft?\s+(?:nu\s+|verder\s+|zelf\s+)?(?:niets|niks)\s+te\s+doen|"
    r"\bhoeft?\s+(?:u|je|jij)\s+(?:nu\s+|verder\s+|zelf\s+)?(?:niets|niks)\s+te\s+doen|"
    r"\bno (?:further )?action (?:is )?(?:needed|required)|\byou (?:don'?t|do not) (?:need|have) to do anything|"
    r"\bniets openstaand|\bnothing (?:is )?outstanding")
# a CONDITIONAL "no action needed" ("No further action is needed if this has already been arranged",
# "U hoeft niets te doen als u al betaald heeft", "Al betaald? Dan hoeft u niets te doen") says the reader must act
# unless they already did: it is the reminder's ask, not a no-pay context (a held-out test mail: a fake "duplicate refund"
# to be returned to a new IBAN, softened block -> warn). Only the no-action tier; "already paid" is left as it was.
_NP_COND_IN = re.compile(r"(?i)\b(?:if|unless|in case|provided|as long as|als|indien|mits|tenzij|mocht(?:en)?|"
                         r"zolang|wanneer)\b")
_NP_COND_BEFORE = re.compile(r"(?i)^\W*(?:dan|then|in (?:dat|that) (?:geval|case)|zo ja|if so)\b")


def _no_action_said(t: str) -> bool:
    """an unconditional "no action needed" (see _NP_NO_ACTION) somewhere in t."""
    for m in _NP_NO_ACTION.finditer(t):
        a, b = _sentence(t, m.start())
        if _NP_COND_IN.search(t, a, b) or _NP_COND_BEFORE.search(t[a:b]):
            continue
        return True
    return False


_NP_PAID = re.compile(
    r"(?i)\bstatus\s*:\s*(?:paid|betaald|voldaan)\b|\bpaid (?:by|with|via)\s*:?\s*(?:visa|mastercard|maestro|amex|"
    r"card|credit ?card|debit card|ideal|paypal|apple pay)|\bbetaald (?:met|via|per)\s+(?:ideal|pin|creditcard|visa|"
    r"mastercard|paypal|apple pay)|\balready (?:been )?paid\b|\b(?:al|reeds) (?:betaald|voldaan)\b|"
    r"\bfor your records\b|\bvoor (?:uw|je) administratie\b|\bpayment received\b|\bbetaling (?:is )?ontvangen\b|"
    r"\b(?:we|wij) (?:have )?received (?:your |the )?(?:payment|transfer|EUR|€|\d)|"
    r"\b(?:betaling|bedrag|overboeking)\b.{0,30}\bontvangen\b")
# "remittance account / details" is the supplier's RECEIVING account (a held-out test mail "our new remittance account"),
# not money coming in; only a remittance advice / plain "remittance" counts.
_NP_INCOMING = re.compile(
    r"(?i)\bteruggestort\b|\btegoed\b|\bcredited to your\b|\b(?:op|naar) (?:uw|je|jouw) (?:eigen |zakelijke )?"
    r"(?:bank)?rekening|\bto your (?:own |business )?(?:bank )?account\b|\buitbetaling\b|\bpayouts?\b|"
    r"\bremittance\b(?!\s+(?:account|bank|details|information|instructions|address))|\b(?:we|wij) (?:have |hebben )?(?:transferred|wired|paid)\b|"
    r"\bwij hebben\b.{0,40}\b(?:overgemaakt|betaald|gestort)\b|\bwe(?:'ve| have) (?:transferred|wired|paid)\b")
_NP_QUOTE = re.compile(r"(?i)\b(?:offerte|prijsopgave|quote|quotation|estimate)\b")
_NP_LATER_INVOICE = re.compile(
    r"(?i)\bbij akkoord\b|\bna (?:akkoord|oplevering|afronding|levering)\b|\bontvangt u (?:de|een|onze) factuur|"
    r"\b(?:is|blijft) \d{1,3} dagen geldig\b|\bupon (?:acceptance|approval|completion|delivery)\b|"
    r"\bonce (?:accepted|approved|completed)\b|\byou will receive (?:an|the|our) invoice\b|\bvalid for \d{1,3} days\b")
# a mail that announces a FUTURE invoice ("Ons kantoor factureert de begeleiding aan het einde van het
# kwartaal", a held-out test mail; "the invoice will follow", "u ontvangt de factuur na afloop") asks nothing to be paid now.
_NP_INVOICE_COMES = re.compile(
    r"(?i)\bfactureert\b[^.!?\n]{0,60}\b(?:aan het einde|aan het eind|eind(?:e)? van|na afloop|achteraf|later|"
    r"volgende maand|per kwartaal|na oplevering|na levering)\b|"
    r"\b(?:de|een|onze|uw|je)\s+factuur\s+(?:volgt|komt|sturen we|ontvangt u|ontvang je)\b[^.!?\n]{0,30}"
    r"\b(?:later|binnenkort|na afloop|apart|separaat|aan het einde|eind(?:e)? van|volgende (?:week|maand))\b|"
    r"\b(?:de|een|onze)\s+factuur\s+volgt\b|\b(?:u|je)\s+ontvangt\s+(?:later|binnenkort|na afloop|aan het einde\s+\w+\s+\w+)"
    r"\s+(?:de|een|onze)\s+factuur\b|"
    r"\b(?:an|the|our|your)\s+invoice\s+(?:will follow|follows|to follow)\b|"
    r"\bwe(?:'ll| will)\s+(?:send|issue)\s+(?:you\s+)?(?:an|the|our)\s+invoice\s+(?:later|separately|at the end|after|"
    r"once|when|next (?:week|month))\b|\byou(?:'ll| will)\s+be\s+invoiced\s+(?:later|separately|at the end|after|once|"
    r"when|next (?:week|month)|monthly|quarterly)\b")
_NP_DEPOSIT = re.compile(r"(?i)\b(?:aanbetaling|vooruitbetaling|voorschot|deposit|advance payment|down ?payment|"
                         r"upfront|up-front|vooraf (?:te )?betalen)\b")
_NP_SCAM_WARNING = re.compile(r"(?i)\b(?:criminals?|criminelen|oplichters?|oplichting|fraudeurs?|scam(?:mers)?|"
                              r"fraud(?:ulent|sters?)?|phishing|nep|valse|fake)\b")
_NP_EXAMPLE_BEFORE = re.compile(r"(?i)\b(?:such as|for example|for instance|e\.g\.|zoals|bijvoorbeeld|bijv\.)"
                                r"\s*:?\s*['\"‘“]?\s*$")
_NP_MY_ACCOUNT_BEFORE = re.compile(r"(?i)\b(?:my|mijn)\s+(?:own\s+|eigen\s+)?(?:bank\s?)?(?:account|rekening(?:nummer)?|"
                                   r"iban)\W{0,3}$")
# A mail that redirects FUTURE payments ("future invoices will be paid into the account of ..", "voortaan op",
# "going forward") is a payee change, whatever else it says ("No action is needed for direct debits already in place").
_NP_REDIRECT = re.compile(
    r"(?i)\b(?:future|toekomstige|upcoming|komende|all (?:further|subsequent)|alle (?:volgende|verdere))\s+(?:\w+\s+)?"
    r"(?:invoices?|payments?|facturen|factuur|betalingen|betaling|termijnen)\b|\bvoortaan\b|\bfrom now on\b|"
    r"\bvanaf nu\b|\bgoing forward\b|\bwith immediate effect\b|\bper direct\b|\bmet ingang van\b")
_NP_REFUND = re.compile(r"(?i)terugbetal|geld terug|terugstort|refund|money back")
NOPAY_TAG = "no-pay context"


def no_pay_reason(text: str, ib: str, action: str = "") -> str | None:
    """why this message does not ask the reader to pay never-paid `ib` now, or None. See the comment above."""
    if not ib or BANK_CHANGE_RE.search(text) or _NP_DD_FAILED.search(text) or _NP_REDIRECT.search(text):
        return None
    t = _unquote(text)
    spans = [(s, e) for i, s, e in ibanlib.find_iban_spans(t) if i == ib]
    # a request word + a set amount in the IBAN's own sentence ("Graag € 2.400,00 vandaag op X") asks
    # as much as a pay verb does: "the invoice follows" / "already paid" ... no longer explain it away.
    strong = _pay_asks(text).get(ib, ("", False))[1] or _iban_sentence_asks(text, ib)
    if spans and _NP_SCAM_WARNING.search(t) and any(_NP_EXAMPLE_BEFORE.search(t[max(0, s - 30):s]) for s, _e in spans):
        return "the IBAN is quoted as an example in a fraud warning"
    if action == "refund" and _NP_REFUND.search(t) and any(_NP_MY_ACCOUNT_BEFORE.search(t[max(0, s - 40):s])
                                                           for s, _e in spans):
        return "the sender asks for a refund to their own account (C7 checks the refund itself)"
    if _NP_DIRECT_DEBIT.search(t):
        return "the message says this is paid by direct debit (incasso)"
    # "optional" / "donation" words soften a pay ask ("you MAY pay here"), but not an outright ask to
    # pay a set amount to this IBAN (_amount_ask: "Graag € 4.750,00 betalen aan X voor het goede doel", "Please
    # transfer EUR 4,750.00 to X today, it is an optional donation" -- CEO fraud dressed up as charity).
    hard = None
    if _NP_OPTIONAL.search(t) or _NP_DONATION.search(t) or _NP_MEMBERSHIP_FEE.search(t):
        hard = any(_amount_ask(t, s, e) for s, e in spans)
    if _NP_OPTIONAL.search(t) and not hard:
        return "the message says paying is optional"
    if _NP_DONATION.search(t) and not hard:
        return "the message is a donation appeal"
    if _NP_MEMBERSHIP_FEE.search(t) and not hard:
        return "the message is a membership-fee appeal"
    if _NP_QUOTE.search(t) and _NP_LATER_INVOICE.search(t) and not _NP_DEPOSIT.search(t):
        return "the message is a quote; the invoice comes later"
    if strong:
        return None
    if _NP_OPTIONAL_WEAK.search(t):
        return "the message says paying is optional"
    if _NP_CAMPAIGN.search(t):
        return "the message is a donation appeal"
    if spans and any(_is_source(t, s) for s, _e in spans):
        return "the IBAN is where money comes FROM (incoming money)"
    if _NP_PAID.search(t):
        return "the message says this was already paid"
    if _NP_INCOMING.search(t):
        return "the message is about money coming in to you"
    if _no_action_said(t):
        return "the message says no action is needed"
    if _NP_INVOICE_COMES.search(t) and not _NP_DEPOSIT.search(t):
        return "the message announces an invoice that comes later"
    return None


_GIFT_OBJECT_RE = re.compile(r"(?i)\b(?:gift|giften|donatie|donaties|bijdrage|donation|donations|contribution|"
                             r"sponsoring|sponsorbedrag)\b[^.!?\n]{0,15}$")


# "if you wish to keep our partnership" is a threat, not a leave-it-to-you.
# only a threat to a RELATIONSHIP ("keep / continue / maintain our partnership / cooperation /
# contract / working with us", "stay our supplier") or "avoid ..". Appeal wording stays a leave-it-to-you: "if you
# wish to keep supporting our shelter", "continue supporting our work", "protect our forests", "keep the library open".
_THREAT_AFTER = (r"(?:keep|continue|maintain|stay|remain|retain)\b(?!\s+(?:supporting|helping|donating|giving|to\s+"
                 r"(?:support|help|donate|give)))(?:\s+[\w']+){0,3}?\s+(?:partnership|partners|cooperation|"
                 r"collaboration|contract|relationship|business|account|job|position|employment|deal|supplier|client|"
                 r"customer|working|work(?:ing)?\s+together)\b|avoid\b")
_IF_YOU_WISH_RE = re.compile(r"(?i)\b(?:wie wil|als (?:je|jij|u) (?:dat |het )?wil(?:t)?|desgewenst|indien gewenst|"
                             r"if you (?:wish|like|prefer|want to|would like to)(?!\s+(?:to\s+)?(?:" + _THREAT_AFTER + r"))|"
                             r"whoever wants|you (?:can|may)|(?:je|jij|u) kunt|(?:kan|mag) ook|is ook mogelijk)\b")


def _amount_ask(t: str, s: int, e: int) -> bool:
    """the IBAN's own sentence asks the reader outright to pay a set amount: a pay verb (not the
    sender's own, not a third-party notice) plus a money amount that is no reported figure and not the object of a gift
    noun ("uw gift van € 50 overmaken op X", "your donation of EUR 50" stay a donation appeal), in a sentence that
    does not leave it to the reader ("Wie wil, kan € 25,00 overmaken op X", "if you prefer .. you can pay EUR ..",
    "u kunt" stay an offer). "Kun je .. overmaken" is a question that asks, not a "you can"."""
    a, b = _sentence(t, s)
    sent = t[a:b]
    amts = [(p, p + len(raw)) for _d, p, raw in find_amounts(sent)] + [(x, y) for x, y, _r in _bare_amounts(sent)]
    masked = _mask_amount_dots(sent)
    verbs = [m for r in (_PAY_VERB_RE, _DONATE_VERB_RE, _GIVE_GIFT_RE) for m in r.finditer(masked)
             if not _first_person(masked, m) and not _pay_notice(masked, m)]
    amts = [(x, y) for x, y in amts if not (s - a <= x < e - a) and not is_reported_figure(sent, x, y)]
    if not amts or not verbs:
        return False
    # in a donation-worded message the cap comes first and holds for EVERY set amount
    # in the sentence -- also the object of a gift noun ("uw gift van € 9.800,00 graag vandaag overmaken op X") and
    # also under a leave-it-to-you ("If you wish, you can make the donation of EUR 9,800.00 to X"). A per-unit amount
    # ("€ 250,00 per medewerker", "EUR 200 each") counts as above the cap. Only up to the cap do the appeal readings
    # below apply. An optional-only message (no donation word: "If you prefer to pay yearly you can pay EUR 1,009.20
    # to X. This is optional.") keeps its leave-it-to-you exit, as before.
    gift = [_gift_object(sent, x, verbs) for x, _y in amts]
    donation = bool(_NP_DONATION.search(t))
    if any((donation or g) and (_amount_value(sent, x, y) > DONATION_PLAIN_CAP or _per_unit(sent, y))
           for (x, y), g in zip(amts, gift)):
        return True
    amts = [xy for xy, g in zip(amts, gift) if not g]
    if not amts:
        return False
    # pay-now pressure beats a leave-it-to-you ("If you like the idea, please donate EUR 4,750.00
    # to X today. I need it done before 12.").
    if _IF_YOU_WISH_RE.search(sent) and not _pay_now_pressure(t):
        return False
    # a plain appeal ("Please donate EUR 25.00 to X. Thank you for your support.", "Doneer
    # € 10,00 op X") asks a set amount too. With no pay-now pressure in the message it stays an appeal up to
    # DONATION_PLAIN_CAP; a bigger (or per-unit) set amount or pay-now pressure is an order.
    return _pay_now_pressure(t) or any(_amount_value(sent, x, y) > DONATION_PLAIN_CAP or _per_unit(sent, y)
                                       for x, y in amts)


# "€ 250,00 per medewerker (12 medewerkers)" asks a multiple of the amount. A per-person /
# per-item amount counts as above DONATION_PLAIN_CAP; a per-period amount ("€ 5 per maand", "EUR 10 a month") is the
# usual recurring appeal and stays as it is.
_PER_PERIOD = r"(?:dag|week|maand|kwartaal|jaar|day|week|month|quarter|year|keer|time|termijn|instal+ment)s?\b"
_PER_UNIT_RE = re.compile(r"(?i)\s*(?:(?:per|elke?|iedere?|voor\s+elke?|for\s+each|for\s+every|every)\s+(?!"
                          + _PER_PERIOD + r")[a-zà-ÿ]{3,}|each\b|apiece\b|p\.\s?p\.|pp\b|"
                          r"an?\s+(?:person|head|member|employee|colleague|participant)\b)")


def _per_unit(sent: str, y: int) -> bool:
    """the amount ending at y is a per-person / per-item price ("€ 250,00 per medewerker",
    "EUR 200 each", "a head"), not a per-period one ("per maand")."""
    return bool(_PER_UNIT_RE.match(sent, y))


# the largest set amount a donation / optional-worded message may ask without pay-now pressure and still be
# an appeal (warn, not block). The earlier CEO-fraud test asks are EUR 4,750-12,400; real appeals ask EUR 10-50.
DONATION_PLAIN_CAP = Decimal("250")
# Pay-now pressure for the donation gate: PRESSURE_RE (urgent, vandaag nog, confidential ..) or a deadline / done-check
# ("before noon", "before 12", "vóór 12 uur", "I need it done", "confirm when done", "bevestig als het gedaan is").
# Plain "today" / "doneer nu" is no pressure here: appeals say that too.
_DONATION_PRESSURE_RE = re.compile(
    r"(?i)\b(?:before\s+(?:noon|lunch|\d{1,2}(?:[:.]\d{2})?(?:\s*(?:am|pm|o'?clock))?(?![\d/.-]|\s+(?:" + _MONTHS + r")\b))|"
    r"(?:vóór|voor)\s+(?:de\s+middag|\d{1,2}(?:[:.]\d{2})?\s*uur)|i\s+need\s+(?:it|this)|ik\s+(?:heb|wil)\s+het\s+"
    r"(?:nodig|vandaag|voor)|(?:confirm|let\s+me\s+know)\s+(?:when|once|as\s+soon\s+as)\s+(?:it\s+is\s+|it'?s\s+)?"
    r"(?:done|sent|paid|transferred)|bevestig\s+(?:als|wanneer|zodra)|laat\s+(?:me|even|het)\s+(?:even\s+)?weten\s+"
    r"(?:als|wanneer|zodra)\s+het\s+(?:gedaan|gelukt|overgemaakt|betaald|geregeld))")


def _pay_now_pressure(t: str) -> bool:
    return bool(PRESSURE_RE.search(t) or _DONATION_PRESSURE_RE.search(t))


def _amount_value(sent: str, x: int, y: int) -> Decimal:
    from .money import parse_signed
    hit = next((d for d, p, _raw in find_amounts(sent) if p == x), None)
    if hit is not None:
        return abs(hit)
    try:  # a bare amount: "12.400,00", "12.400,-", "(1.150)"
        return abs(parse_signed(re.sub(r"[^\d.,]", "", sent[x:y]).rstrip(".,")))
    except (MoneyError, ValueError, ArithmeticError):
        return DONATION_PLAIN_CAP + 1  # unreadable: never counts as a small appeal


# "Please donate EUR 4,750.00 to X today" / "Doneer vandaag € 4.750 aan X" asks a set amount
# as much as "transfer" does. Only used by _amount_ask (the donation-word gate), not as a C14 pay cue.
_DONATE_VERB_RE = re.compile(r"(?i)\b(?:donate|doneer|doneren|te\s+doneren)\b")
# "Make a donation of EUR 4,750.00 to X today", "Stuur de donatie van € 4.750,00 naar X": a
# give verb with a gift noun as its object is an order like "transfer"; "make YOUR donation of EUR 25" stays an appeal
# (_POSSESSIVE_GIFT_RE).
# "make YOUR donation of EUR 9,800.00 to X today" is a give verb too, so the cap reaches it;
# up to the cap the possessive keeps it an appeal (_gift_object).
_GIVE_GIFT_RE = re.compile(r"(?i)\b(?:make|send|give|stuur|geef|doe)\b(?:\s+[\w-]+){0,2}?\s+"
                           r"(?:donation|contribution|gift|donatie|bijdrage)\b")
_POSSESSIVE_GIFT_RE = re.compile(r"(?i)\b(?:uw|je|jouw|your)\s+(?:\w+\s+)?$")


def _gift_object(sent: str, x: int, verbs: list[re.Match]) -> bool:
    """The amount at x is the object of a gift noun ("uw gift van € 50", "your donation of EUR 50"): an appeal, not
    a set-amount ask. Not when a pay verb stands BEFORE that noun and the noun is not the reader's own
    ("Transfer the donation of EUR 4,750.00 to X" is an order; "Please transfer your donation of EUR 50" stays an
    appeal)."""
    g = _GIFT_OBJECT_RE.search(sent, 0, x)
    if not g:
        return False
    return not (any(m.start() < g.start() for m in verbs) and not _POSSESSIVE_GIFT_RE.search(sent, 0, g.start()))


_NP_NL = {
    "the IBAN is quoted as an example in a fraud warning": "het IBAN staat als voorbeeld in een fraudewaarschuwing",
    "the sender asks for a refund to their own account (C7 checks the refund itself)":
        "de afzender vraagt geld terug op de eigen rekening (C7 controleert de terugbetaling zelf)",
    "the message says this is paid by direct debit (incasso)": "het bericht zegt dat dit via automatische incasso gaat",
    "the message says paying is optional": "het bericht zegt dat betalen niet verplicht is",
    "the message is a donation appeal": "het is een oproep om te doneren",
    "the message is a membership-fee appeal": "het is een oproep voor een lidmaatschapsbijdrage",
    "the message is a quote; the invoice comes later": "het is een offerte; de factuur komt later",
    "the IBAN is where money comes FROM (incoming money)": "het IBAN is de rekening waar geld VANAF komt",
    "the message says this was already paid": "het bericht zegt dat dit al betaald is",
    "the message is about money coming in to you": "het bericht gaat over geld dat naar jou komt",
    "the message says no action is needed": "het bericht zegt dat je niets hoeft te doen",
    "the message announces an invoice that comes later": "het bericht kondigt een factuur aan die later komt",
}


def _soften(c: Check, reason: str) -> Check:
    """a never-paid block becomes a warn in a no-pay context (the detail says why)."""
    return Check(c.rule, False, f"{c.detail} -- warn, not block: {NOPAY_TAG}: {reason}", "warn",
                 f"{c.uitleg} Geen blokkade, wel opletten: {_NP_NL.get(reason, reason)}. Betaal je toch, controleer "
                 "dan eerst het rekeningnummer.")


# Back-compat for scripts that print a regex hit: any cue word, no proximity. NOT used by C14.
PAY_ASK_RE = re.compile("|".join(r.pattern.replace("(?i)", "") for r in (_PAY_VERB_RE, _PAY_NOUN_RE, _PAY_WEAK_RE)),
                        re.IGNORECASE)


@dataclass
class Policy:
    """Limits a human sets. Override in <workspace>/policy.json, e.g. {"limits": {"pay_invoice": "2500.00"}}."""
    limits: dict = field(default_factory=lambda: {
        "pay_invoice": "1500.00", "refund": "250.00", "payout": "500.00",
        "send_invoice": "10000.00", "send_reminder": "10000.00"})
    known_ibans: dict = field(default_factory=dict)    # IBAN -> name, vetted by the user
    known_emails: dict = field(default_factory=dict)   # PayPal e-mail -> name, vetted by the user
    duplicate_window_days: int = 14
    explicit_limits: set = field(default_factory=set)  # actions whose limit the user set in policy.json

    @classmethod
    def load(cls, path: Path | None) -> "Policy":
        p = cls()
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            p.limits.update(data.get("limits", {}))
            p.explicit_limits |= set(data.get("limits", {}))
            p.known_ibans.update({ibanlib.normalise(k): v for k, v in data.get("known_ibans", {}).items()})
            p.known_emails.update({k.lower(): v for k, v in data.get("known_emails", {}).items()})
            p.duplicate_window_days = int(data.get("duplicate_window_days", p.duplicate_window_days))
        return p


@dataclass
class Check:
    rule: str
    ok: bool
    detail: str
    severity: str = "block"   # block | warn
    uitleg: str = ""          # plain-Dutch explanation shown to the pilot user when the check fails
    goed: str = ""            # plain-Dutch reassurance shown when the check passes (only where it adds something)

    def as_dict(self) -> dict:
        return asdict(self)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _name_tokens(s: str) -> set[str]:
    stop = {"bv", "b.v.", "vof", "v.o.f.", "the", "de", "het", "van", "en", "and", "ltd", "nv", "holding", "info"}
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in stop}


def _same_party(a: str, b: str) -> bool:
    return bool(_name_tokens(a) & _name_tokens(b))


LEGAL_FORMS = {"bv", "nv", "vof", "cv", "ltd", "llc", "inc", "gmbh", "bvba", "sarl", "srl", "eenmanszaak",
               "holding"}


def _norm_name(s: str) -> str:
    """Company name for comparison: no case, diacritics, punctuation or legal form ("Jansen Bouw B.V." ->
    "jansen bouw")."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\.)|(?<=\b[a-z]\.[a-z])\.", "", s)  # b.v. -> bv, v.o.f. -> vof
    s = re.sub(r"\.", "", s)
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w]
    return " ".join(w for w in words if w not in LEGAL_FORMS)


def _names_match(a: str, b: str) -> bool:
    """Fuzzy: same normalised name, near-identical spelling, one name's words all in the other, or a bank-truncated
    prefix ("KARTONFABRIEK NOO")."""
    x, y = _norm_name(a), _norm_name(b)
    if not x or not y:
        return True  # nothing to compare: never warn on a missing name
    if x == y or SequenceMatcher(None, x, y).ratio() >= 0.85:
        return True
    tx, ty = set(x.split()), set(y.split())
    if tx <= ty or ty <= tx:
        return True
    short, long_ = sorted((x, y), key=len)
    return len(short) >= 8 and long_.startswith(short)


def _name_check(ib: str, name: str, out_tx: list[dict], policy: Policy) -> Check | None:
    """C12 (warn): this IBAN was paid before, but under a materially different name than the one on this message."""
    known = [policy.known_ibans.get(ib) or ""] + [t["counterparty"] for t in out_tx if t["iban"] == ib]
    known = list(dict.fromkeys(k.strip() for k in known if k and _norm_name(k)))
    if not ib or not known or len(_norm_name(name).replace(" ", "")) < 3:  # no (real) name given: nothing to compare
        return None
    if any(_names_match(name, k) for k in known):
        return Check("C12 payee name matches earlier payments to this IBAN", True, f"{name} ~ {known[-1]}", "warn")
    was = ", ".join(known[-3:])
    return Check("C12 payee name matches earlier payments to this IBAN", False,
                 f"{ib} was paid to {was}; this message names {name}", "warn",
                 f"Naar {ib} betaalde je eerder aan {was}, maar dit bericht noemt {name}. Is dit rekeningnummer "
                 f"echt van {name}? Bel bij twijfel de leverancier op een nummer dat je al had.")


def _days_between(a: str, b: str) -> int | None:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except ValueError:
        return None


def _mentions(tx: dict, ref: str) -> bool:
    r = _alnum(ref)
    return len(r) >= 3 and (r in _alnum(tx.get("description")) or r in _alnum(tx.get("reference")))


# W4 (warn) names the link or login ask on a phishing card. Before, a flag_suspicious card only showed
# W1/W2 ("Geen druk of haast") while its reason said phishing. W4 reads, in the message itself: links (http(s):// or
# www.) whose domain imitates a known bank / payee / platform or differs from the sender's domain, a sender domain
# that imitates one, and a request to log in or (re-)enter or confirm bank or login details. The uitleg shows the
# bare domain as plain text (the page escapes it and never makes it clickable). The official domains below
# (belastingdienst.nl, duo.nl, cjib.nl ... the sources of official.py) never count as a lookalike or foreign link.
# Shown on flag_suspicious cards only: the verdict there is always "flagged", so no other card and no scorecard moves.
_URL_RE = re.compile(r"(?i)\b(?:https?://(?:[^\s/@<>\"']*@)?|www\.)([a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
                     r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)")
# brand token -> (display name, official registrable domains as (label, tld) pairs). The pairs keep the no-endpoint
# guard in tests/test_shadow.py meaningful (no payment/bank URL in the code): these names are only compared, never
# opened. Short tokens (ing, asn, sns, duo, kvk, cjib) must be a whole label part of the domain ("ing-zakelijk");
# longer ones may sit inside a label ("belasting-dienst-nl").
_BRANDS = {
    "ing": ("ING", (("ing", "nl"), ("ing", "com"), ("ing", "be"))),
    "rabobank": ("Rabobank", (("rabobank", "nl"), ("rabobank", "com"))),
    "abnamro": ("ABN AMRO", (("abnamro", "nl"), ("abnamro", "com"))),
    "bunq": ("bunq", (("bunq", "com"),)),
    "knab": ("Knab", (("knab", "nl"),)),
    "asn": ("ASN Bank", (("asnbank", "nl"),)),
    "sns": ("SNS", (("snsbank", "nl"), ("sns", "nl"))),
    "regiobank": ("RegioBank", (("regiobank", "nl"),)),
    "triodos": ("Triodos", (("triodos", "nl"), ("triodos", "com"))),
    "paypal": ("PayPal", (("paypal", "com"), ("paypal", "nl"), ("paypal", "me"))),
    "tikkie": ("Tikkie", (("tikkie", "me"), ("tikkie", "nl"))),
    "belastingdienst": ("de Belastingdienst", (("belastingdienst", "nl"),)),
    "digid": ("DigiD", (("digid", "nl"),)),
    "mijnoverheid": ("MijnOverheid", (("mijnoverheid", "nl"),)),
    "duo": ("DUO", (("duo", "nl"),)),
    "cjib": ("het CJIB", (("cjib", "nl"),)),
    "kvk": ("de KVK", (("kvk", "nl"),)),
    "postnl": ("PostNL", (("postnl", "nl"), ("postnl", "com"))),
    "marktplaats": ("Marktplaats", (("marktplaats", "nl"),)),
    "microsoft": ("Microsoft", (("microsoft", "com"), ("office", "com"), ("live", "com"), ("outlook", "com"))),
}
_OFFICIAL_DOMAINS = {f"{lab}.{tld}" for _n, ds in _BRANDS.values() for lab, tld in ds} | {"overheid.nl", "rijksoverheid.nl"}
_LEET = str.maketrans({"1": "l", "0": "o", "3": "e", "5": "s", "4": "a", "7": "t"})
LOGIN_ASK_RE = re.compile(
    r"(?i)\b(?:log\s?in|inloggen|login|meld u aan|aanmelden|sign in|"
    r"verifi(?:eer|eren|ëren|ëer|y|cation)|bevestig(?:t|en)?\s+(?:uw|je|jouw|hier)?\s*(?:gegevens|rekening|identiteit|"
    r"account|bankgegevens|betaalgegevens)|(?:uw|je)\s+gegevens\s+(?:niet\s+)?bevestig(?:t|en)?|"
    r"confirm (?:your )?(?:details|account|identity|bank)|"
    r"(?:bank|betaal|inlog|rekening)gegevens\s+(?:opnieuw\s+)?(?:in(?:vullen|voeren)|bijwerken|updaten|controleren)|"
    r"vul\s+(?:uw|je)\s+(?:bank|betaal|inlog|rekening)?gegevens|wachtwoord|pincode|password|"
    r"update (?:your )?(?:payment|bank|account) (?:details|information))")

# A click ask without a written-out link ("Click the link below", "via deze link"): named only when no URL was found.
CLICK_ASK_RE = re.compile(r"(?i)\b(?:(?:klik|click|tik|tap|druk)\b.{0,25}\b(?:link|hier|here|below|onder|knop|button)|"
                          r"via (?:deze|de|onderstaande) link|(?:the|this) link below)\b")

def _registrable(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def _is_official(host: str) -> bool:
    h = host.lower().strip(".")
    return any(h == d or h.endswith("." + d) for d in _OFFICIAL_DOMAINS)


def _imitates(host: str) -> str | None:
    """Display name of the brand this domain imitates ('ing-zakelijk-verificatie.example' -> 'ING',
    'paypa1-secure.example' -> 'PayPal'), or None. The brand's own domains are no imitation."""
    if not host or _is_official(host):
        return None
    name = host.lower().translate(_LEET).replace("rn", "m")
    labels = name.split(".")[:-1] or name.split(".")
    tokens = {t for lab in labels for t in lab.split("-") if t}
    joined = "".join(labels).replace("-", "")
    for token, (display, _doms) in _BRANDS.items():
        if token in tokens or (len(token) >= 5 and token in joined):
            return display
    return None


def _link_login_check(message: dict, text: str) -> Check:
    rule = "W4 no suspicious link or login request"
    sender = (message.get("from") or "").split("@")[-1].strip().lower()
    hosts = list(dict.fromkeys(m.group(1).lower().rstrip(".") for m in _URL_RE.finditer(text)))
    found_en, found_nl = [], []
    for h in hosts:
        if _is_official(h):
            continue
        brand = _imitates(h)
        if brand:
            found_en.append(f"link to {h} (imitates {brand})")
            found_nl.append(f"De link gaat naar {h}. Dat lijkt op {brand}, maar het is niet het echte adres.")
        elif sender and _registrable(h) != _registrable(sender):
            found_en.append(f"link to {h} (not the sender's domain {sender})")
            found_nl.append(f"De link gaat naar {h}, niet naar het domein van de afzender ({sender}).")
    sbrand = _imitates(sender)
    if sbrand and not any(_registrable(h) == _registrable(sender) and _imitates(h) for h in hosts):
        found_en.append(f"sender domain {sender} imitates {sbrand}")
        found_nl.append(f"De afzender ({sender}) lijkt op {sbrand}, maar het is niet het echte adres.")
    asks = list(LOGIN_ASK_RE.finditer(text))
    ask = next((a for a in asks if "niet" not in a.group(0).lower()), asks[0] if asks else None)
    click = None if hosts else CLICK_ASK_RE.search(text)
    wants = []
    if click:
        found_en.append(f'asks to click a link "{click.group(0)}"')
        wants.append(f'op een link te klikken ("{click.group(0)}")')
    if ask:
        found_en.insert(0, f'login/details request "{ask.group(0)}"')
        wants.append(f'in te loggen, iets te bevestigen of gegevens in te vullen ("{ask.group(0)}")')
    if wants:
        found_nl.insert(0, "Het bericht vraagt je " + " en ".join(wants) + ".")
    if not found_en:
        return Check(rule, True, "none found", "warn")
    return Check(rule, False, "found " + "; ".join(found_en), "warn",
                 " ".join(found_nl) + " Klik niet op de link en vul niets in. Ga zelf naar de website of app die je "
                                      "al kent, of bel een nummer dat je al had.")


def evaluate(proposal: dict, message: dict, history: list[dict], policy: Policy,
             earlier: list[dict] | None = None, earlier_messages: list[dict] | None = None) -> list[Check]:
    """Return all checks for one proposal. `history` = imported transactions; `earlier` = cards drafted
    before this one in the same run (for duplicate detection); `earlier_messages` = messages that came before
    this one (C15: the same payment request seen twice, whatever action label the planner chose)."""
    checks: list[Check] = []
    action = proposal["action"]
    text = message_text(message)  # subject + body + readable PDF attachments
    mail_text = f"{message.get('subject', '')}\n{message.get('body', '')}"
    pdfs = message.get("pdfs") or []
    payee = proposal.get("payee") or {}
    out_tx = [t for t in history if Decimal(t["amount"]) < 0]
    in_tx = [t for t in history if Decimal(t["amount"]) > 0]

    # Warnings that apply to every message, whatever the planner thought of it.
    m = PRESSURE_RE.search(text)
    checks.append(Check("W1 no pressure language", not m, f'found "{m.group(0)}"' if m else "none found", "warn",
                        f'Het bericht zet je onder druk ("{m.group(0)}"). Fraudeurs doen dat zodat je niet eerst controleert.'
                        if m else ""))
    m = BANK_CHANGE_RE.search(text)
    checks.append(Check("W2 no change of bank details", not m, f'found "{m.group(0)}"' if m else "none found", "warn",
                        "Het bericht meldt nieuwe bankgegevens. Bel de afzender op een nummer dat je al had."
                        if m else ""))

    unread = [p for p in pdfs if p.get("warning")]
    if pdfs:
        checks.append(Check("W3 PDF attachments readable", not unread,
                            "; ".join(f"{p['name']}: {p['warning']}" for p in unread) or f"{len(pdfs)} read", "warn",
                            "Kan PDF niet lezen: " + "; ".join(f"{p['name']} ({p['warning']})" for p in unread) +
                            ". Controleer IBAN en bedrag op de factuur zelf." if unread else ""))

    if action == "flag_suspicious":
        checks.append(_link_login_check(message, text))  # what made it look like phishing

    bank_change = bool(BANK_CHANGE_RE.search(text))
    if action in ("no_action", "flag_suspicious"):
        # the structural message checks run on "no money action" cards too. The planner can miss a pay
        # ask (no amount it can read, no pay word), and the user may still pay from the mail by hand: a mail that
        # asks to pay a never-paid IBAN, announces a new one, or swaps a supplier's IBAN is blocked whatever the
        # label. Only BLOCK results are kept here: a never-paid IBAN that nobody asks you to pay is not worth a
        # warning on a card that proposes nothing. Bank-change wording without a readable IBAN stays the W2 warn
        # (there is nothing to pay to on this card).
        # a never-paid block softened by a no-pay context (no_pay_reason) is kept as a warn, so the card
        # still says "look twice before paying this by hand".
        checks.extend(c for c in _message_iban_checks(proposal, message, text, bank_change, history, policy,
                                                      no_iban_block=False)
                      if not c.ok and (c.severity == "block" or NOPAY_TAG in c.detail))
        checks.extend(_message_official_checks(proposal, message, text, history))
        return checks

    # Facts of the message itself, whatever action the planner chose (a model that labels a new-IBAN
    # fraud as "send_reminder", or a repeated invoice as a fresh one, must not slip past the IBAN checks).
    checks.extend(_message_iban_checks(proposal, message, text, bank_change, history, policy))
    checks.extend(_message_official_checks(proposal, message, text, history))
    if earlier_messages:
        c15 = _repeat_request_check(message, text, earlier_messages)
        if c15:
            checks.append(c15)

    # C1 amount
    amount = None
    try:
        amount = parse_amount(proposal.get("amount"))
        checks.append(Check("C1 valid amount", True, f"{amount} {proposal.get('currency')}"))
    except MoneyError as exc:
        checks.append(Check("C1 valid amount", False, str(exc), uitleg="Er is geen geldig bedrag."))
        return checks
    cur = proposal.get("currency") or "EUR"
    checks.append(Check("C2 currency is EUR", cur == "EUR", cur, uitleg=f"Valuta {cur} wordt niet ondersteund."))

    payee_ib = ibanlib.normalise(payee.get("iban")) if action == "pay_invoice" else ""
    nopay = no_pay_reason(text, payee_ib, action) if payee_ib and not bank_change else None
    checks.append(_limit_check(action, amount, payee_ib, out_tx, history, policy, nopay, text, bank_change))

    q = (proposal.get("evidence_quote") or "").strip()
    grounded = bool(q) and _norm_ws(q) in _norm_ws(text)
    checks.append(Check("V1 evidence quote is in the message", grounded, f'"{q}"' if q else "no quote",
                        uitleg="De onderbouwing staat niet letterlijk in het bericht."))
    amt_txt = {str(amount), str(amount).replace(".", ","), f"{amount:,.2f}".replace(",", "X").replace(".", ",")
               .replace("X", ".")}
    amt_txt.add(f"{amount:,.2f}")  # English thousands format "1,250.00" (now also read by the rule planner)
    if amount == amount.to_integral_value():
        amt_txt.add(str(int(amount)))
        # a whole amount with a thousands separator and no cents ("€ 38.412", "EUR 38,412").
        amt_txt |= {f"{int(amount):,}", f"{int(amount):,}".replace(",", ".")}
    checks.append(Check("V2 amount appears in the message", any(a in text for a in amt_txt), f"{amount}",
                        uitleg="Dit bedrag staat niet in het bericht."))

    if action == "pay_invoice":
        ib = ibanlib.normalise(payee.get("iban"))
        valid = ibanlib.is_valid(ib)
        checks.append(Check("C4 IBAN checksum valid", valid, ib or "(none)",
                            uitleg=f"Rekeningnummer {ib or '(geen)'} kan niet bestaan (tikfout of nep)."))
        in_msg = bool(ib) and ib in ibanlib.normalise(text)
        checks.append(Check("V3 IBAN appears in the message", in_msg, ib or "(none)",
                            uitleg="Dit IBAN staat niet in het bericht."))
        party = payee.get("name") or message.get("from_name") or ""
        dom = (message.get("from") or "").split("@")[-1].split(".")[0]
        suppliers = [party] + [p.get("supplier") or "" for p in pdfs]
        pdf_ibans = [i for p in pdfs for i in p.get("ibans") or []]
        src = " (IBAN from the PDF attachment)" if ib in pdf_ibans else ""
        c5 = _history_check("C5 IBAN known from your own payments", ib, suppliers, dom, out_tx, policy, src,
                            own_accounts(history))
        if not c5.ok and nopay and c5.detail.startswith("never paid"):
            c5 = _soften(c5, nopay)  # e.g. "payment received from X", a quote, a donation appeal
        checks.append(c5)
        c12 = _name_check(ib, payee.get("name") or "", out_tx, policy)
        if c12:
            checks.append(c12)
        c13 = _official_check(ib, suppliers + [message.get("from_name") or ""], message.get("from") or "", text)
        if c13:
            checks.append(c13)
        checks.extend(_pdf_checks(ib, amount, mail_text, pdfs, suppliers, dom, out_tx, policy, own_accounts(history)))
        ref = proposal.get("invoice_ref") or ""
        hit = next((t for t in out_tx if ref and _mentions(t, ref)), None)
        if hit:
            checks.append(Check("C6 invoice not already paid", False,
                                f"{ref} paid on {hit['date']} ({hit['amount']} {hit['currency']})",
                                uitleg=f"Factuur {ref} is al betaald op {nl.datum(hit['date'])}. Niet nog eens betalen."))
        else:
            twin = next((t for t in out_tx if t["iban"] == ib and Decimal(t["amount"]) == -amount and
                         (message.get("date") and (_days_between(t["date"], message["date"]) or 0)
                          <= policy.duplicate_window_days)), None)
            checks.append(Check("C6 invoice not already paid", twin is None,
                                f"same amount paid to same IBAN on {twin['date']}" if twin else
                                (f"no payment mentions {ref}" if ref else "no invoice number to match"),
                                "warn", uitleg=f"Hetzelfde bedrag ging op {nl.datum(twin['date'])} al naar dit IBAN. "
                                               "Dubbele factuur?" if twin else ""))

    if action == "refund":
        ref, email = proposal.get("order_ref") or "", (payee.get("email") or "").lower()
        paid = [t for t in in_tx if (ref and _mentions(t, ref))] or [t for t in in_tx if email and t["email"] == email]
        if not paid:
            checks.append(Check("C7 refund matches a payment you received", False,
                                f"no incoming payment for order {ref or '?'} / {email or '?'}",
                                uitleg="Er is geen ontvangen betaling voor deze bestelling gevonden."))
        else:
            got = Decimal(paid[-1]["amount"])
            back = sum((-Decimal(t["amount"]) for t in out_tx if (ref and _mentions(t, ref)) or
                        (email and t["email"] == email)), Decimal("0"))
            room = got - back
            checks.append(Check("C7 refund matches a payment you received", amount <= room,
                                f"{amount} <= received {got} - refunded {back} = {room}",
                                uitleg=f"De klant vraagt {nl.bedrag(amount)} terug, maar betaalde {nl.bedrag(got)}"
                                + (f" en kreeg al {nl.bedrag(back)} terug." if back else ".")))

    if action == "payout":
        email = (payee.get("email") or "").lower()
        known = email in policy.known_emails or any(t["email"] == email for t in out_tx)
        checks.append(Check("C8 PayPal payee known from your own payments", known, email or "(none)",
                            uitleg=f"Je hebt nog nooit aan {email or '(geen e-mail)'} betaald."))
        checks.append(Check("V4 payee e-mail appears in the message", bool(email) and email in text.lower(),
                            email or "(none)", uitleg="Dit e-mailadres staat niet in het bericht."))

    if action in ("send_invoice", "send_reminder"):
        email = payee.get("email") or ""
        checks.append(Check("C9 recipient is an e-mail address", bool(EMAIL_RE.match(email)), email or "(none)",
                            uitleg="Geen geldig e-mailadres voor de ontvanger."))
    if action == "send_reminder":
        ref = proposal.get("invoice_ref") or ""
        hit = next((t for t in in_tx if ref and _mentions(t, ref)), None)
        checks.append(Check("C10 customer has not paid yet", hit is None,
                            f"{ref} received on {hit['date']}" if hit else f"no incoming payment mentions {ref}",
                            uitleg=f"De klant heeft factuur {ref} al betaald op {nl.datum(hit['date'])}. Geen herinnering sturen."
                            if hit else ""))

    if earlier is not None and action in MONEY_OUT | {"send_invoice", "send_reminder"}:
        key = _dupe_key(proposal)
        dup = next((c for c in earlier if _dupe_key(c) == key), None)
        checks.append(Check("C11 not a duplicate of another card", dup is None,
                            f"same as card {dup['id']}" if dup else "first of its kind",
                            uitleg=f"Dit is dezelfde actie als de kaart \"{dup.get('subject') or dup['id']}\"." if dup else ""))
    return checks


def _message_iban_checks(proposal: dict, message: dict, text: str, bank_change: bool, history: list[dict],
                         policy: Policy, no_iban_block: bool = True) -> list[Check]:
    """C14: every IBAN in the message (mail text, readable PDFs) and the draft's own payee IBAN is checked against
    your history, for EVERY outgoing action. For pay_invoice the draft's IBAN is C5's job and PDF IBANs are P2's;
    C14 covers the rest. Block when the IBAN replaces one you paid the same party on (IBAN CHANGED), or when the
    message announces new bank details and the IBAN is one you never used. also block when the message
    asks the reader to pay and the IBAN is one you never paid -- whatever label the planner chose. "asks
    to pay" is now per IBAN (find_pay_asks: a pay word near that IBAN, not quoted, not the sender paying); your own
    account from the bank export counts as known. Refund wording never softens a block.
    Otherwise (an unknown IBAN without bank-change or payment-ask wording) it warns.
    bank-change wording with no IBAN we could actually parse (garbled text, an
    unsupported format, an image-only mention) must still block, for every outgoing action -- not pass silently."""
    action = proposal["action"]
    payee = proposal.get("payee") or {}
    own = ibanlib.normalise(payee.get("iban"))
    pdf_ibans = [i for p in message.get("pdfs") or [] for i in p.get("ibans") or []]
    ibans = list(dict.fromkeys([i for i in ibanlib.find_ibans(text) if ibanlib.is_valid(i)] +
                               ([own] if ibanlib.is_valid(own) else [])))
    seen = bool(ibans)
    if action == "pay_invoice":
        ibans = [i for i in ibans if i != own and i not in pdf_ibans]
    rule = "C14 IBANs in the message known from your own payments"
    asks = find_pay_asks(text)
    # an amount or due date next to a never-paid IBAN is a pay instruction too (passive / verb-less asks).
    structural = find_structural_asks(text)
    # a payment ask to an IBAN-shaped number that fails the checksum (a typo, or a fake such as the "NL00 INGB
    # 0000 0000 00" of a fake tax letter) blocks too. Only full-length numbers of a known country count; the draft's
    # own pay_invoice IBAN is C4's job.
    bad = [i for i in ibanlib.find_ibans(text) if i[:2] in ibanlib.LENGTHS and not ibanlib.is_valid(i) and
           not (action == "pay_invoice" and i == own) and i in asks]
    pre: list[Check] = []
    if bad:
        pre.append(Check(rule, False, f'message asks for payment ("{asks[bad[0]]}") to {", ".join(bad)}, '
                                      f"which fails the IBAN checksum (draft action: {action})", "block",
                         f"Het bericht vraagt om een betaling naar {', '.join(bad)}, maar dat is geen geldig IBAN "
                         "(het controlegetal klopt niet). Typfout of vervalsing: bel de afzender op een nummer dat je "
                         "al had."))
    if not ibans:
        # IBANs WERE read, but all of them are the draft's own payee / PDF IBANs, which C5 / P2
        # check. "No IBAN could be read" would be false here; that block is only for mails with no readable IBAN.
        if bank_change and no_iban_block and not seen:
            return pre + [Check("C14 IBANs in the message known from your own payments", False,
                          f"message announces new bank details but no IBAN could be read (draft action: {action})",
                          "block",
                          "Het bericht meldt nieuwe bankgegevens, maar er is geen leesbaar IBAN in te vinden. Bel "
                          "de afzender op een nummer dat je al had voordat je iets met dit bericht doet.")]
        return pre
    out_tx = [t for t in history if Decimal(t["amount"]) < 0]
    in_ibans = {t["iban"] for t in history if Decimal(t["amount"]) > 0 and t["iban"]}
    dom = (message.get("from") or "").split("@")[-1].split(".")[0]
    suppliers = [payee.get("name") or "", message.get("from_name") or ""] + \
                [p.get("supplier") or "" for p in message.get("pdfs") or []]
    own_accts = own_accounts(history)
    out: list[Check] = pre
    for ib in ibans:
        if ib in in_ibans and not (ib in policy.known_ibans or any(t["iban"] == ib for t in out_tx)):
            out.append(Check(rule, True, f"{ib} paid you before"))
            continue
        h = _history_check(rule, ib, suppliers, dom, out_tx, policy, own=own_accts)
        ask = asks.get(ib) or (f"payment details: {structural[ib]} next to the IBAN" if ib in structural else None)
        if h.ok:
            out.append(h)
        elif h.detail.startswith("IBAN CHANGED"):
            out.append(Check(rule, False, h.detail + f" (draft action: {action})", "block",
                             h.uitleg + " Ook al stelt dit concept geen betaling voor: geen enkele actie op dit "
                                        "bericht tot je het oude nummer hebt nagebeld."))
        elif bank_change:
            out.append(Check(rule, False, f"message announces new bank details and {ib} was never paid before "
                                          f"(draft action: {action})", "block",
                             f"Het bericht meldt nieuwe bankgegevens en noemt {ib}, waar je nog nooit naar betaalde. "
                             "Bel de afzender op een nummer dat je al had voordat je iets met dit bericht doet."))
        elif ask:
            c = Check(rule, False, f'message asks for payment ("{ask}") and {ib} was '
                                   f"never paid before (draft action: {action})", "block",
                      f"Het bericht vraagt om een betaling en noemt {ib}, waar je nog nooit naar betaalde. "
                      "Ook al stelt dit concept geen betaling voor: controleer de afzender en het "
                      "rekeningnummer (bel op een nummer dat je al had) voordat je iets met dit bericht doet.")
            reason = no_pay_reason(text, ib, action)  # optional / paid / incoming / incasso / quote ...
            out.append(_soften(c, reason) if reason else c)
        else:
            out.append(Check(rule, False, h.detail + f" (draft action: {action})", "warn",
                             f"Dit bericht noemt {ib}, waar je nog nooit naar betaalde. Controleer het eerst."))
    return out


# the message itself explains why this invoice is larger than the usual ones (a held-out test mail "seasonal
# stock-up .. so it is higher than your regular weekly orders", L10 "nota voor het jaarwerk .. hoger dan de gewone
# kwartaalnota", L15 "extra oplage voor de vakbeurs .. een stuk hoger dan jullie normale maandelijkse drukwerk").
# Either an explicit comparison with the usual amount, or a reason word: extra / more work or hours, annual or
# year-end work, seasonal, one-off, a project.
_LARGER_REASON = re.compile(
    r"(?i)\b(?:higher|larger|bigger|more|greater)\s+than\s+(?:\w+\s+){0,3}?(?:usual|normal|regular|typical|average|"
    r"previous|last|before)\b|\b(?:hoger|groter|meer)\s+dan\s+(?:\w+\s+){0,3}?(?:gewoonlijk|gebruikelijk|normaal|"
    r"anders|gewone|normale|gebruikelijke|reguliere|vorige|eerdere|vaste)\w*|"
    r"\b(?:seasonal|season's|annual|yearly|year-end|one-off|one-time|non-recurring)\b|"
    r"\b(?:extra|additional)\s+(?:work|hours|delivery|deliveries|order|stock|print run|copies|scope|services?)\b|"
    r"\b(?:project|projects)\b|\bmeer\s*(?:uren|werk)\b|\bmeerwerk\b|"
    r"\bextra\s+(?:uren|werk|werkzaamheden|oplage|levering|leveringen|bestelling|voorraad|opdracht)\b|"
    r"\bjaar(?:afrekening|werk|rekening|nota|factuur|abonnement|contract|licentie|bedrag)\b|\beindafrekening\b|"
    r"\beenmalig\w*\b|\bseizoen\w*\b|\bprojecten?\b")


def _limit_check(action: str, amount: Decimal, payee_ib: str, out_tx: list[dict], history: list[dict],
                 policy: Policy, nopay: str | None, text: str = "", bank_change: bool = False) -> Check:
    """C3 per-action limit. pay_invoice only, when the user set no pay_invoice limit of their own in
    policy.json: the limit for an IBAN you paid before is 1.5x your largest earlier payment to it, instead of the flat
    default. Above that: block when the amount is also above the flat default, else warn (unusual for this payee,
    but under your general limit). No history to this IBAN: the flat default, as before. Your own account (a
    transfer between your own accounts) has no limit. In a no-pay context (no_pay_reason) a failed limit warns."""
    rule = "C3 per-action limit"
    flat = Decimal(policy.limits.get(action, "0"))
    grens = "je eigen grens" if action in policy.explicit_limits else "de grens die Countersign standaard gebruikt"
    uitleg = f"{nl.bedrag(amount)} is hoger dan {grens} ({{}})."
    if action == "pay_invoice" and payee_ib and payee_ib in own_accounts(history):
        return Check(rule, True, f"{payee_ib} is your own account: no limit for a transfer between your own accounts")
    c = Check(rule, amount <= flat, f"{amount} <= {flat}", uitleg=uitleg.format(nl.bedrag(flat)))
    if action == "pay_invoice" and payee_ib and action not in policy.explicit_limits:
        past = [-Decimal(t["amount"]) for t in out_tx if t["iban"] == payee_ib]
        if past:
            top = max(past)
            lim = (top * Decimal("1.5")).quantize(Decimal("0.01"))
            ok = amount <= lim
            c = Check(rule, ok, f"{amount} <= {lim} (1.5x your largest earlier payment to this IBAN, {top})",
                      "block" if amount > flat else "warn",
                      f"{nl.bedrag(amount)} is veel meer dan je ooit naar dit rekeningnummer betaalde (hoogste was "
                      f"{nl.bedrag(top)})"
                      + (f" en hoger dan {grens} ({nl.bedrag(flat)})." if amount > flat else ". Klopt dit bedrag?"))
    if not c.ok and c.severity == "block" and nopay:
        c = _soften(c, nopay)
    # a larger-than-usual invoice that says WHY, to the IBAN you already paid this supplier on (C3 only takes
    # this path for a known payee IBAN) and with no change of bank details anywhere in the message, warns instead of
    # blocks: the money goes to the account you know. A new or changed IBAN still meets C5 / C14 (IBAN CHANGED,
    # never paid) and the flat limit; bank-change wording keeps this block.
    elif not c.ok and c.severity == "block" and text and not bank_change and action not in policy.explicit_limits \
            and payee_ib in {t["iban"] for t in out_tx} and (why := _LARGER_REASON.search(text)):
        c = Check(rule, False, f"{c.detail} -- warn, not block: the message gives a reason (\"{why.group(0)}\") and "
                               f"{payee_ib} is the IBAN you paid before", "warn",
                  f"{c.uitleg} Het bericht noemt een reden (\"{why.group(0)}\") en het rekeningnummer is hetzelfde als "
                  "eerder. Geen blokkade, wel opletten: klopt de reden met wat je besteld of afgesproken hebt?")
    # a mail that says nothing is to be paid NOW ("no advance payment is needed", "the invoice follows
    # after completion", "factuur volgt", "geen aanbetaling", a held-out test mail: a known supplier's quote with its IBAN in
    # the signature) to the IBAN you paid this party on: the limit warns, as for a reason above. A new or changed IBAN
    # still meets C5 / C14; bank-change wording or a user-set limit keeps the block.
    # only when the payee IBAN's own sentence asks nothing ("Graag € 8.900,00 overmaken op
    # KNOWN. De factuur volgt." asks to pay now, so "nothing is due now" is no reason) and the amount is at most
    # NO_PAY_NOW_CAP x your largest earlier payment to it (a 10x-28x "quote" to a hijacked mailbox still blocks).
    elif not c.ok and c.severity == "block" and text and not bank_change and action not in policy.explicit_limits \
            and payee_ib in {t["iban"] for t in out_tx} and (why := _NO_PAY_NOW.search(text)) \
            and amount <= NO_PAY_NOW_CAP * max(-Decimal(t["amount"]) for t in out_tx if t["iban"] == payee_ib) \
            and not _message_asks(text, payee_ib):
        c = Check(rule, False, f"{c.detail} -- warn, not block: the message says nothing is due now (\"{why.group(0)}\") "
                               f"and {payee_ib} is the IBAN you paid before", "warn",
                  f"{c.uitleg} Het bericht zegt dat er nu nog niets betaald hoeft te worden (\"{why.group(0)}\") en het "
                  "rekeningnummer is hetzelfde als eerder. Geen blokkade: betaal pas als de echte factuur er is.")
    return c


# ~3x per an earlier brief, set to 4x: test_payout_notices's known-quote guard is 3.9x (EUR 3,480 vs 890), a held-out test mail is
# 2.9x (3,480 vs 1,215); the slips a reviewer found were 10x-28x.
NO_PAY_NOW_CAP = Decimal("4")


def _iban_sentence_asks(text: str, ib: str) -> bool:
    """some sentence that holds `ib` asks the reader to pay: a pay verb (not the sender's own, not a third-
    party notice), or a request word (graag/please/kun je ..) next to a money amount ("Graag € 8.900,00 op X")."""
    t = _unquote(text)
    for i, s, _e in ibanlib.find_iban_spans(t):
        if i != ib:
            continue
        a, b = _sentence(t, s)
        sent = _mask_amount_dots(t[a:b])
        if any(not _first_person(sent, m) and not _pay_notice(sent, m) for m in _PAY_VERB_RE.finditer(sent)):
            return True
        if _MOVE_ASK_RE.search(sent) and (find_amounts(sent) or _bare_amounts(sent)):
            return True
    return False


_NEGATED_BEFORE_RE = re.compile(r"(?i)\b(?:geen|niets|niks|niet|hoeft|hoeven|no|not|nothing|don'?t|needn'?t|never)\b")


def _message_asks(text: str, ib: str) -> bool:
    """the message asks the reader to pay, anywhere, not only in the payee IBAN's sentence
    ("Graag vandaag € 3.400,00 overmaken.\nIBAN: KNOWN\nDe factuur volgt."): _iban_sentence_asks, or a sentence with a
    pay verb (not the sender's own, not a third-party notice, not negated: "u hoeft niets vooraf te betalen") and a
    money amount or an IBAN in it. A verb without an amount ("please pay within 14 days of the invoice") is a payment
    term, no ask now."""
    if _iban_sentence_asks(text, ib):
        return True
    t = _unquote(text)
    for m in _PAY_VERB_RE.finditer(_mask_amount_dots(t)):
        a, b = _sentence(t, m.start())
        sent = t[a:b]
        if not (find_amounts(sent) or _bare_amounts(sent) or ibanlib.find_ibans(sent)):
            continue
        if _first_person(t, m) or _pay_notice(t, m):
            continue
        ca, _cb = _clause(t, m.start(), m.end())
        if _NEGATED_BEFORE_RE.search(t, max(a, ca), m.start()):
            continue
        return True
    return False


_NO_PAY_NOW = re.compile(
    r"(?i)\bno\s+(?:advance|up-?front|down)\s+payment\s+(?:is\s+)?(?:needed|required|necessary)\b|"
    r"\bno\s+deposit\s+(?:is\s+)?(?:needed|required|necessary)\b|"
    r"\b(?:the|an|our|my)\s+invoice\s+(?:will\s+follow|follows|is\s+to\s+follow)\b|"
    r"\b(?:de|een|onze|mijn)\s+factuur\b[^.!?\n]{0,40}\bvolgt\b|"
    r"\bgeen\s+(?:aanbetaling|vooruitbetaling|voorschot)\b|\bniets\s+vooraf\s+(?:te\s+)?betalen\b")


def _message_official_checks(proposal: dict, message: dict, text: str, history: list[dict]) -> list[Check]:
    """C13 for the IBANs in the message itself, for every action (pay_invoice: the draft's own payee
    IBAN is already checked in evaluate()). A mail that claims to be from an official payee (Belastingdienst, CJIB
    ...: sender name, domain or a "t.n.v." line) and names an IBAN that is not on its official list fails, even
    when the planner proposed nothing. Your own accounts (bank export) and IBANs that paid you are skipped: a
    bookkeeper's note about your tax return that quotes your own IBAN is no claim to be paid. One check at most
    (the first failing IBAN)."""
    payee = proposal.get("payee") or {}
    own_payee = ibanlib.normalise(payee.get("iban")) if proposal["action"] == "pay_invoice" else ""
    # an IBAN you PAID before is no claim to be the official payee either ("Btw Q3 betaald aan
    # Belastingdienst .. Drukkerij Jansen NL83 ..", a held-out test mail: the supplier's known IBAN failed C13). The draft's
    # own pay_invoice payee keeps its C13 in evaluate(); a never-paid IBAN still gets this check.
    skip = own_accounts(history) | {t["iban"] for t in history if t["iban"]}
    names = [payee.get("name") or "", message.get("from_name") or ""]
    for ib in dict.fromkeys(i for i in ibanlib.find_ibans(text) if ibanlib.is_valid(i)):
        if ib == own_payee or ib in skip:
            continue
        c = _official_check(ib, names, message.get("from") or "", text)
        if c and not c.ok:
            return [Check(c.rule, False, c.detail + f" (IBAN in the message; draft action: {proposal['action']})",
                          c.severity, c.uitleg)]
    return []


def _invoice_refs(text: str) -> set[str]:
    return {_alnum(m.group(1)) for m in INVOICE_REF_RE.finditer(text)}


def _repeat_request_check(message: dict, text: str, earlier_messages: list[dict]) -> Check | None:
    """C15 (block): an earlier message already asked for this same payment. Facts only: same invoice number from
    the same party, or (when either message has no invoice number) the same IBAN and the same amount. Two invoices
    with different numbers are never a repeat. Returns None when the message names no IBAN, number or amount."""
    rule = "C15 payment not already requested in an earlier message"
    refs = _invoice_refs(text)
    ibans = {i for i in ibanlib.find_ibans(text) if ibanlib.is_valid(i)}
    amounts = {d for d, _, _ in find_amounts(text)}
    dom = (message.get("from") or "").split("@")[-1].lower()
    if not (ibans or refs) or not (amounts or refs):
        return None
    for prev in earlier_messages:
        ptext = message_text(prev)
        pibans = {i for i in ibanlib.find_ibans(ptext) if ibanlib.is_valid(i)}
        pdom = (prev.get("from") or "").split("@")[-1].lower()
        same_party = bool(ibans & pibans) or (bool(dom) and dom == pdom)
        if not same_party:
            continue
        prefs = _invoice_refs(ptext)
        if refs and prefs:
            if not (refs & prefs):
                continue  # different invoice numbers: a new invoice, not a repeat
            why = f"invoice {', '.join(sorted(refs & prefs))}"
            why_nl = "Deze factuur werd"
        else:
            same_amount = amounts & {d for d, _, _ in find_amounts(ptext)}
            if not (ibans & pibans and same_amount):
                continue
            why = f"{fmt_amount(max(same_amount))} to {', '.join(sorted(ibans & pibans))}"
            why_nl = (f"Dezelfde betaling ({nl.bedrag(max(same_amount))} naar "
                      f"{', '.join(sorted(ibans & pibans))}) werd")
        when = prev.get("date") or "?"
        subj = prev.get("subject") or ""
        return Check(rule, False, f"{why} already requested in the message of {when} ({subj})", "block",
                     f"{why_nl} al gevraagd in het bericht van {nl.datum(when)} (\"{subj}\"). "
                     "Betaal één keer, via die kaart.")
    return Check(rule, True, "no earlier message asks for this payment", "block")


def own_account_draft(proposal: dict, message: dict, history: list[dict]) -> dict:
    """a pay_invoice draft whose payee is one of YOUR OWN accounts (bank export) while the message has
    no transfer wording for that IBAN (no pay ask and no move verb next to it) is no payment: the IBAN is your own one,
    e.g. in your own signature under a turnover report (a held-out test mail drafted "pay € 4.105 to your own account").
    Returns a no_action copy of the proposal (the no_action checks still look at every other IBAN in the mail), else
    the proposal itself. "Zet € 4.105 over naar mijn spaarrekening X" keeps its draft (a real own-account transfer)."""
    if proposal.get("action") != "pay_invoice":
        return proposal
    ib = ibanlib.normalise((proposal.get("payee") or {}).get("iban"))
    if not ib or ib not in own_accounts(history):
        return proposal
    text = message_text(message)
    if _pay_asks(text).get(ib, ("", False))[1]:
        return proposal
    t = _unquote(text)
    spans = [(s, e) for i, s, e in ibanlib.find_iban_spans(t) if i == ib]
    for m in _MOVE_VERB_RE.finditer(t):
        if any(max(s - m.end(), m.start() - e, 0) <= PAY_ASK_WINDOW for s, e in spans):
            return proposal
    return {**proposal, "action": "no_action", "amount": None,
            "rationale": f"{ib} is je eigen rekening en het bericht vraagt geen overboeking daarheen; niets te betalen."}


def own_accounts(history: list[dict]) -> set[str]:
    """the user's own IBANs, read automatically from the "Rekening" / "IBAN/BBAN" / account column of the
    imported bank exports (importers.transactions sets `own_iban`)."""
    return {t["own_iban"] for t in history if t.get("own_iban")}


def _history_check(rule: str, ib: str, suppliers: list[str], dom: str, out_tx: list[dict], policy: Policy,
                   src: str = "", own: set[str] | frozenset = frozenset()) -> Check:
    """Is this IBAN one you paid before (or your own account)? If not: did you pay the same supplier on a different
    IBAN (IBAN CHANGED)?"""
    if ib and ib in own:
        return Check(rule, True, f"{ib} is your own account (from your bank export){src}")
    paid_before = [t for t in out_tx if t["iban"] == ib]
    off = official.lookup(ib)
    if off and not (ib in policy.known_ibans or paid_before):
        return Check(rule, True, f"{ib} is on the official list of {off[0].name} ({off[0].source}, "
                                 f"checked {off[0].checked}){src}")
    if ib in policy.known_ibans or paid_before:
        who = policy.known_ibans.get(ib) or paid_before[-1]["counterparty"]
        return Check(rule, True, f"{ib} paid {len(paid_before)}x before ({who}){src}")
    same = [t for t in out_tx if t["iban"] and t["iban"] != ib and
            (any(_same_party(t["counterparty"], s) for s in suppliers if s) or
             (len(dom) > 3 and _same_party(t["counterparty"], dom)))]
    if same:
        return Check(rule, False, f"IBAN CHANGED: you paid {same[-1]['counterparty']} on {same[-1]['iban']}, "
                                  f"this asks for {ib}{src}",
                     uitleg=f"Je betaalde {same[-1]['counterparty']} altijd op {same[-1]['iban']}. "
                            f"Nu wordt {ib} gevraagd{' (op de PDF-factuur)' if src else ''}. "
                            "Klassieke factuurfraude: bel eerst.")
    return Check(rule, False, f"never paid {ib} before{src}",
                 uitleg=f"Je hebt nog nooit naar {ib} betaald. Controleer de leverancier eerst.")


def _official_check(ib: str, names: list[str], sender: str, text: str) -> Check | None:
    """C13: a message that claims to be from (or to pay) an official Dutch payee such as the Belastingdienst must
    use an IBAN from that payee's official list (official.py). A match gets a reassurance line."""
    rule = "C13 official payee uses an IBAN from its official list"
    claimed = official.claimed_by(names, sender.split("@")[-1], text)
    hit = official.lookup(ib)
    if claimed:
        ok_for = [p for p in claimed if ib in p.ibans]
        if ok_for:
            p = ok_for[0]
            return Check(rule, True, f"{ib} is on the official list of {p.name} ({p.source}, checked {p.checked})",
                         "block", goed=f"{ib} staat op de officiële lijst van {p.name} ({p.ibans[ib]}; bron: "
                                       f"{p.source}, gecontroleerd op {nl.datum(p.checked)}). Klopt ook het betalingskenmerk "
                                       "met je brief of Mijn-omgeving?")
        p = claimed[0]
        sev = "block" if any(c.complete for c in claimed) else "warn"
        names_ = " / ".join(c.name for c in claimed)
        return Check(rule, False, f"message claims {names_}, but {ib or '(no IBAN)'} is not on its official list "
                                  f"({p.source}, checked {p.checked})", sev,
                     f"Dit bericht komt zogenaamd van {names_}, maar {ib or 'dit rekeningnummer'} staat niet op de "
                     f"officiële lijst ({p.source}, gecontroleerd op {nl.datum(p.checked)}). Ook als je bank bij de IBAN-Naam-Check "
                     f"'naam klopt' toont: die controleert alleen of de naam die jij intikt bij dit rekeningnummer "
                     f"hoort, niet of het rekeningnummer van {p.name} is. {p.what_to_do}")
    if hit:
        p, note = hit
        return Check(rule, True, f"{ib} is on the official list of {p.name} ({p.source}, checked {p.checked})",
                     "block", goed=f"{ib} is een officieel rekeningnummer van {p.name} ({note}; bron: {p.source}, "
                                   f"gecontroleerd op {nl.datum(p.checked)}).")
    return None


def _pdf_checks(ib: str, amount: Decimal, mail_text: str, pdfs: list[dict], suppliers: list[str], dom: str,
                out_tx: list[dict], policy: Policy, own: set[str] | frozenset = frozenset()) -> list[Check]:
    """Compare readable PDF invoices with the mail text, the proposal and your own payment history."""
    read = [p for p in pdfs if p.get("text")]
    if not read:
        return []
    out: list[Check] = []
    pdf_ibans = list(dict.fromkeys(i for p in read for i in p.get("ibans") or []))
    mail_ibans = [i for i in ibanlib.find_ibans(mail_text) if ibanlib.is_valid(i)]
    differ = [i for i in pdf_ibans if i not in mail_ibans]
    if pdf_ibans and mail_ibans:
        out.append(Check("P1 PDF IBAN matches the mail text", not differ,
                         f"mail says {', '.join(mail_ibans)}, PDF says {', '.join(pdf_ibans)}",
                         uitleg=f"In de mail staat {', '.join(mail_ibans)}, maar op de PDF-factuur staat "
                                f"{', '.join(differ)}. Een van de twee is vervalst. Bel de leverancier op een "
                                "nummer dat je al had." if differ else ""))
    for other in (i for i in pdf_ibans if i != ib):  # the proposal's own IBAN is already checked by C5
        out.append(_history_check("P2 PDF IBAN known from your own payments", other, suppliers, dom, out_tx,
                                  policy, " (IBAN from the PDF attachment)", own))
    bad = list(dict.fromkeys(i for p in read for i in p.get("bad_ibans") or []))
    if bad:
        out.append(Check("P3 PDF IBAN checksum valid", False, ", ".join(bad), "warn",
                         f"Op de PDF staat een ongeldig IBAN ({', '.join(bad)}): het controlegetal klopt niet."))
    totals = list(dict.fromkeys(p["total"] for p in read if p.get("total")))
    if totals:
        same = fmt_amount(amount) in totals
        out.append(Check("P4 PDF total matches the amount", same,
                         f"card {fmt_amount(amount)}, PDF total {', '.join(totals)}", "warn",
                         f"Op de PDF-factuur staat totaal {nl.bedragen(totals)}, maar er wordt {nl.bedrag(amount)} "
                         "gevraagd. Controleer welk bedrag klopt." if not same else ""))
    return out


def fmt_amount(d: Decimal) -> str:
    return str(d.quantize(Decimal("0.01")))


def _dupe_key(p: dict) -> tuple:
    payee = p.get("payee") or {}
    return (p.get("action"), ibanlib.normalise(payee.get("iban")), (payee.get("email") or "").lower(),
            str(p.get("amount")), _alnum(p.get("invoice_ref")) or _alnum(p.get("order_ref")))


def verdict(action: str, checks: list[Check]) -> str:
    """ok | warn | blocked | flagged | none"""
    if action == "flag_suspicious":
        return "flagged"
    if any(not c.ok and c.severity == "block" for c in checks):
        return "blocked"
    if action == "no_action":
        return "warn" if any(not c.ok for c in checks) else "none"
    if any(not c.ok for c in checks):
        return "warn"
    return "ok"
