# SPDX-License-Identifier: Apache-2.0
"""Offline rule planner (default). Deterministic, Dutch + English, no network, no model.

It is deliberately naive: it believes what a message says (the amount, the IBAN, "our bank details changed").
Catching lies is the job of the code checks in checks.py, which look at the user's own transaction history.
"""
from __future__ import annotations

import re

from ..iban import find_iban_spans, find_ibans
from ..money import MoneyError, find_amounts, find_bare_amounts, fmt, is_reported_figure, parse_signed
from . import empty_proposal, message_text

# start an address only where its run of local-part characters starts. Without the lookbehind every
# position of a long letter run was retried up to the end of the run (quadratic: ~4.7 s on 100,000 letters).
# Differs only for addresses glued onto a previous one with no separator ("a@b.cc-x@y.zz").
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
INVOICE_REF_RE = re.compile(
    r"(?i)\b(?:factuur(?:nummer|nr\.?)?|invoice(?:\s*(?:no\.?|number|nr\.?|#))?|nota(?:nummer)?|kenmerk)"
    r"\s*[:#]?\s*((?=[A-Z0-9/-]*\d)[A-Z0-9][A-Z0-9/-]{2,})")
ORDER_REF_RE = re.compile(
    r"(?i)\b(?:order|bestelling|bestelnummer|ordernummer)\s*(?:nr\.?|nummer|number|no\.?)?\s*[:#]?\s*"
    r"((?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{2,})")
TOTAL_HINT = re.compile(r"(?i)(totaal|te betalen|openstaand(?:e)? bedrag|bedrag|total|amount due|fee|honorarium)")

# "incassorekening" (a supplier's COLLECTION account, a held-out test mail "via onze Europese incassorekening") is
# where you are asked to pay, not a direct debit.
P_DIRECT_DEBIT = re.compile(r"(?i)automatisch (?:afgeschreven|geïncasseerd)|incasso(?![-\s]?rekening)|direct debit|"
                            r"wordt .{0,30}afgeschreven")
P_REFUND = re.compile(r"(?i)terugbetal|geld terug|terugstort|refund|retour(?:neren|zending)?|money back")
P_REMINDER_OUT = re.compile(r"(?i)herinnering (?:sturen|versturen)|stuur .{0,40}herinnering|send (?:a |them a )?reminder|aanmanen")
P_SEND_INVOICE = re.compile(
    r"(?i)(?:stuur|stuurt|zend).{0,25}factuur|graag (?:een )?factuur|factuur (?:sturen|graag)|"
    r"send (?:us |me )?(?:an |the |a )?invoice|please invoice|invoice us")
# the infinitive "betalen" too ("Geen aanbetaling nodig, maar wel graag vandaag betalen." was no pay context).
# checks._PAY_VERB also reads storten/voldoen (NL) and transfer/settle (EN) as pay verbs, but this
# planner gate did not, so the same "no amount/invoice word, just the verb" shape stayed silent (none) instead of
# drafting pay_invoice (same gap as the earlier "betalen" fix: the warn-level checks never ran). Added here too, each as a
# whole word (\b...\b): bare "stort" must not match inside the passive participle "gestort" (F11PassiveAskNameBlock,
# test_heldout_regressions), which stays on checks.py's own structural C14 detection instead of this simple gate.
# round 2 of the same diff: wire/remit/deposit (EN) and overboeken/overschrijven (NL) were also in
# checks._PAY_VERB but not here. Same \b...\b discipline: bare "deposit" must not match "depositogarantie", bare
# "overboeken"/"overschrijven" must not match the noun forms "overboeking(en)"/"overschrijving(en)" (already read
# separately by checks._PAY_NOUN_RE). Split forms ("over te boeken/schrijven") and the German überweisen/zahlen
# pair left out of scope.
P_PAY_CONTEXT = re.compile(r"(?i)factuur|invoice|herinnering|aanmaning|nota|te betalen|betaal|betalen|overmaken|"
                           r"\bstorten\b|\bstort\b|\bvoldoen\b|\bvoldoe\b|\btransfer\b|\bsettle\b|pay\b|payment|"
                           r"\bwire\b|\bremit\b|\bdeposit\b|\boverboeken\b|\boverschrijven\b")
P_PHISHING = re.compile(
    r"(?i)(klik|click|log ?in|inloggen|verifieer|verify|bevestig uw|confirm your|link)"
    r"[\s\S]{0,300}(geblokkeerd|blocked|suspend|opgeschort|verloopt|expire|beperkt|limited|vrijgeven|release)|"
    r"(geblokkeerd|blocked|suspend|opgeschort|beperkt|limited)[\s\S]{0,300}(klik|click|log ?in|inloggen|verifieer|verify)|"
    r"(?:betaling|payment) .{0,40}(?:in behandeling|on hold|pending).{0,200}(?:verzend|ship|tracking)|"
    r"(?:betaal|pay)\b.{0,30}\b(?:eerst|first)\b.{0,80}(?:link|tikkie|betaalverzoek)")
P_PAYOUT = re.compile(r"(?i)\bfee\b|pay (?:me|to)|betaal .{0,30}aan|overmaken (?:naar|aan)|paypal|honorarium|my rate")


def _sentence_with(text: str, needle: str) -> str:
    for line in text.splitlines():
        for s in re.split(r"(?<=[.!?])\s+", line):
            if needle and needle in s:
                return s.strip()
    return ""


# an amount WITHOUT a currency sign counts only right after a payment word on the same line ("Totaal:
# 312,40", "Amount due: 1,250.00", "het openstaande bedrag van factuur GH-7802 is 143,20", "(amount 640.00)", "te
# betalen 450"). Dates, times, phone numbers, IBAN digits and invoice / order numbers are no amounts: the bare form
# needs cents or a thousands separator (money.BARE_AMOUNT_RE), and a plain integer only counts directly after the
# payment word, followed by the end of the line or a punctuation mark ("Totaal 3 items" / "te betalen binnen 14
# dagen" are not amounts). No payment word -> no amount: "levering op 12.11.2026", "kenmerk 2026.104".
BARE_HINT = re.compile(r"(?i)\b(?:totaal(?:bedrag)?|subtotaal|te betalen|(?:factuur|eind|totaal|openstaand(?:e)?\s)?"
                       r"bedrag|saldo|restant(?:bedrag)?|total|amount(?: due)?|balance(?: due)?|sum|due|fee|"
                       r"honorarium)\b")
# "saldo per 31.10", "due 15.11", "betalen voor 01.12": a day.month after a date word is a date, not an amount.
_DATE_LIKE = re.compile(r"(?:0?[1-9]|[12]\d|3[01])[.,](?:0?[1-9]|1[0-2])")
_DATE_BEFORE = re.compile(r"(?i)\b(?:per|op|on|due|voor|vóór|before|uiterlijk|tot|until|by|datum|date|d\.d\.)\s*:?\s*$")
_INT_AFTER_HINT = re.compile(r"[ \t]*(?:[:=][ \t]*)?(\d{1,6})(?:,-)?(?=\s*(?:$|[.;)!]\s|[.;)!]?$|\n))", re.M)


def _bare_main_amount(text: str):
    """(Decimal, raw) of the first sign-less amount that follows a payment word on its line, else None."""
    best = None
    for a, b, raw in find_bare_amounts(text):
        if is_reported_figure(text, a, b):  # "omzet 48.600,00", "12.500,00 doel gehaald"
            continue
        ls = text.rfind("\n", 0, a) + 1
        before = text[max(ls, a - 40):a]
        if BARE_HINT.search(before) and not (_DATE_LIKE.fullmatch(raw) and _DATE_BEFORE.search(before)):
            try:
                d = parse_signed(raw.removesuffix(",-"))
            except MoneyError:
                continue
            if d > 0:
                best = best or (a, d, raw)
                break
    for m in BARE_HINT.finditer(text):
        n = _INT_AFTER_HINT.match(text, m.end())
        if n and is_reported_figure(text, n.start(1), n.end(1)):
            continue
        if n and (best is None or n.start(1) < best[0]) and int(n.group(1)) > 0:
            best = (n.start(1), parse_signed(n.group(1)), n.group(1))
            break
    return (best[1], best[2]) if best else None


_NET_LINE = re.compile(r"(?i)\b(?:subtota(?:al|l)\b|excl\b|exclusief\b|excluding\b|exclusive of\b|ex\.?\s+(?:btw|vat)\b|net(?:to)?\s+"
                       r"(?:amount|bedrag)\b)")
# a refund that the SENDER already made to you ("A refund of €86.50 has been sent to the account you paid from",
# "we hebben het bedrag teruggestort", a held-out test mail) is money coming in: nothing for you to refund. A scam that adds
# a fee to "release" such a refund still meets C14 on the no_action card (a pay ask to a never-paid IBAN blocks).
P_REFUND_DONE = re.compile(
    r"(?i)\brefund\b[^!?\n]{0,40}?\b(?:has|have)\s+been\s+(?:sent|issued|processed|paid|transferred|credited|refunded)\b|"
    r"\b(?:we|wij)(?:'ve|\s+have|\s+hebben)?\s+(?:processed|issued|sent|refunded|credited)\b[^.!?\n]{0,30}\brefund\b|"
    r"\b(?:we|wij)\s+hebben\b[^.!?\n]{0,50}\b(?:teruggestort|terugbetaald|terugbetaling\s+(?:verwerkt|verstuurd))\b|"
    r"\b(?:terugbetaling|bedrag)\b[^.!?\n]{0,40}\b(?:is|wordt)\s+(?:teruggestort|terugbetaald|overgemaakt\s+naar\s+(?:uw|je))\b")


def _main_amount(text: str):
    # a reported figure ("omzet € 48.600", "voorbelasting € 6.120", "€ 12.500 opgehaald") is
    # never the pay amount, wherever it sits; what is left follows the usual order (pay label first, else largest).
    amounts = [a for a in find_amounts(text) if not is_reported_figure(text, a[1], a[1] + len(a[2]))]
    if not amounts:
        return _bare_main_amount(text)
    floor = 0  # end of the last net-amount line; its "totaal" word is no hint for the next line's amount
    for d, pos, raw in amounts:  # an amount right after "totaal" / "te betalen" / "amount due" wins
        ls = text.rfind("\n", 0, pos) + 1
        # "Totaal excl. btw: € 1.735,00" is not what you pay (a held-out test mail drafted the net amount, then
        # the "Btw 21%" line below it); the "Totaal: € 2.099,35" line further down is.
        if _NET_LINE.search(text, ls, pos):
            le = text.find("\n", pos)
            floor = le if le >= 0 else len(text)
            continue
        before = text[max(0, pos - 40, floor):pos]
        if TOTAL_HINT.search(before):
            return d, raw
    best = max(amounts, key=lambda a: a[0])
    return best[0], best[2]


_PARA_RE = re.compile(r"\n[ \t]*\n")


def _payee_amount(text: str, ib: str, mail_end: int):
    """the amount asked in the paragraph of the payee IBAN, else None. A mail that lists figures in
    another paragraph ("Samenvatting:\nTe betalen aan de Belastingdienst: € 5.537,75" above "Mijn factuur .. € 425,00
    op NL92 ..", a held-out test mail; "Uitgaven evenementen: € 12.980" above "De contributie blijft € 120 .. Graag
    overmaken op NL09 ..", L13) asks for the amount next to the IBAN, not for the largest figure in the mail. The usual
    pick (_main_amount) runs on that paragraph only, and only when it holds a currency amount that is no reported
    figure; otherwise (an IBAN-only line, a details block, a one-paragraph mail, an IBAN only in a PDF) the whole-mail
    pick stands."""
    spans = [(s, e) for i, s, e in find_iban_spans(text) if i == ib and e <= mail_end]  # mail text, not a PDF
    if not spans:
        return None
    s = spans[-1][0]
    a = max((m.end() for m in _PARA_RE.finditer(text, 0, s)), default=0)
    m = _PARA_RE.search(text, s)
    b = m.start() if m else len(text)
    if a == 0 and b == len(text):
        return None
    para = text[a:b]
    if not [x for x in find_amounts(para) if not is_reported_figure(para, x[1], x[1] + len(x[2]))]:
        return None
    return _main_amount(para)


def _other_email(text: str, sender: str) -> str:
    emails = [e.lower() for e in EMAIL_RE.findall(text)]
    others = [e for e in emails if e != sender]
    return (others[-1] if others else "") or sender


# the no-pay contexts that mean "nothing to pay now" (checks.no_pay_reason). Not planned away:
# "optional" / "donation" (the reader MAY choose to pay: keep the pay draft, the checks warn), direct debit (handled
# above), the fraud-warning example and the refund-to-own-account context (other actions).
_PLANNER_NOPAY = {
    "the message is a quote; the invoice comes later": "het is een offerte; de factuur komt later",
    "the IBAN is where money comes FROM (incoming money)": "het IBAN is de rekening waar geld VANAF komt",
    "the message says this was already paid": "het bericht zegt dat dit al betaald is",
    "the message is about money coming in to you": "het bericht gaat over geld dat naar jou komt",
    "the message says no action is needed": "het bericht zegt dat je niets hoeft te doen",
    "the message announces an invoice that comes later": "het bericht kondigt een factuur aan die later komt",
}


def _no_pay_planner_reason(text: str, ib: str) -> str | None:
    """Dutch reason why this pay-looking message asks nothing to be paid to `ib` now, else None."""
    from ..checks import no_pay_reason  # checks imports this module: import lazily
    return _PLANNER_NOPAY.get(no_pay_reason(text, ib, "pay_invoice") or "")


class RulePlanner:
    name = "rules (offline)"

    def plan_one(self, msg: dict) -> dict:
        text = message_text(msg)
        sender = (msg.get("from") or "").lower()
        p = empty_proposal(msg["id"])
        inv = INVOICE_REF_RE.search(text)
        order = ORDER_REF_RE.search(text)
        main = _main_amount(text)
        ibans = find_ibans(text)
        if main:
            p["amount"] = fmt(main[0])
            p["evidence_quote"] = _sentence_with(text, main[1])
        p["invoice_ref"] = inv.group(1) if inv else ""
        p["order_ref"] = order.group(1) if order else ""
        name = msg.get("from_name") or sender

        if P_DIRECT_DEBIT.search(text):
            p.update(action="no_action", amount=None, rationale="Wordt via automatische incasso betaald; niets te doen.")
        elif P_REFUND.search(text) and main and order and P_REFUND_DONE.search(text):
            p.update(action="no_action", amount=None,
                     rationale="De terugbetaling is al gedaan, naar jou toe; niets terug te betalen.")
        elif P_REFUND.search(text) and main and order:
            p["action"] = "refund"
            p["payee"] = {"name": name, "iban": ibans[-1] if ibans else "", "email": sender}
            p["rationale"] = f"Klant vraagt {p['amount']} EUR terug voor bestelling {p['order_ref']}."
        elif P_REMINDER_OUT.search(text) and inv:
            p["action"] = "send_reminder"
            p["payee"] = {"name": "", "iban": "", "email": _other_email(text, sender)}
            p["rationale"] = f"Onze factuur {p['invoice_ref']} lijkt onbetaald; concept voor een vriendelijke herinnering."
        elif P_SEND_INVOICE.search(text) and main:
            p["action"] = "send_invoice"
            p["payee"] = {"name": name, "iban": "", "email": _other_email(text, "") or sender}
            p["rationale"] = "Klant vraagt om een factuur."
        elif ibans and main and P_PAY_CONTEXT.search(text) and (np := _no_pay_planner_reason(text, ibans[-1])):
            # a receipt, payment confirmation, remittance advice, quote or statement with money coming
            # in names an IBAN and an amount, but asks nothing to be paid now. Draft no_action with a warning; the
            # checks still look at every IBAN in the mail (C14 keeps its block / softened warn on no_action cards).
            p.update(action="no_action", amount=None,
                     rationale=f"Geen betaalverzoek: {np}. Let op: {ibans[-1]} staat in het bericht; betaal er niet "
                               "naar zonder het rekeningnummer eerst te controleren.")
        elif ibans and main and P_PAY_CONTEXT.search(text):
            p["action"] = "pay_invoice"
            p["payee"] = {"name": name, "iban": ibans[-1], "email": sender}
            anchored = _payee_amount(text, ibans[-1], len(f"{msg.get('subject', '')}\n{msg.get('body', '')}"))  # the amount next to the payee IBAN
            if anchored and anchored[0] != main[0]:
                p["amount"] = fmt(anchored[0])
                p["evidence_quote"] = _sentence_with(text, anchored[1])
            p["rationale"] = (f"Factuur{' ' + p['invoice_ref'] if p['invoice_ref'] else ''} vraagt "
                              f"{p['amount']} EUR op {ibans[-1]}.")
        elif P_PHISHING.search(text):
            p.update(action="flag_suspicious", amount=None,
                     rationale="Lijkt op phishing: druk plus een link of inlogverzoek. Niet klikken.")
        elif main and P_PAYOUT.search(text):
            p["action"] = "payout"
            p["payee"] = {"name": name, "iban": "", "email": _other_email(text, "") or sender}
            p["rationale"] = f"Bericht vraagt {p['amount']} EUR te betalen aan {p['payee']['email']}."
        else:
            p.update(action="no_action", amount=None, evidence_quote="", rationale="Geen betaalactie nodig.")
        return p

    def plan(self, messages: list[dict]) -> list[dict]:
        return [self.plan_one(m) for m in messages]
