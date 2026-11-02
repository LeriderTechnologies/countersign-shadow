# SPDX-License-Identifier: Apache-2.0
"""Synthetic test set: a fictional pottery webshop in Utrecht ("Studio Klei", owner Sanne).

Everything here is invented: people, companies, domains (*.example), orders and bank accounts. IBANs are built
with a valid checksum (except the deliberately broken one) but the account numbers are made up.

`write_samples(folder)` creates:
    inbox/*.eml          21 messages, Dutch and English
    inbox.mbox           the same messages as one mailbox (to test the .mbox importer)
    paypal.csv           PayPal activity download (English headers)
    ing.csv              ING CSV (semicolon version, with "Saldo na mutatie" and "Tag")
    rabobank.csv         Rabobank CSV (26 columns per the 2022 format description)
    abnamro.txt          ABN AMRO TXT (tab-separated, no header)
EXPECTED maps each message file to the action and verdict the rule planner should produce.
"""
from __future__ import annotations

import csv
import io
import mailbox
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

from .iban import make_nl

OWN = make_nl("INGB", "0006543210")
KARTON = make_nl("INGB", "0001234567")        # packaging supplier, paid before
GLAZUUR = make_nl("RABO", "0312456789")       # glaze supplier, paid before
BOEKHOUDER = make_nl("ABNA", "0456123789")    # bookkeeper, paid monthly
FRAUD = make_nl("BUNQ", "2087654321")         # "new IBAN" fraud
CEO_FRAUD = make_nl("TRIO", "0198765432")     # fake boss
BROKEN = "NL00INGB0000000000"                  # checksum fails on purpose
CAFE = make_nl("RABO", "0155667788")          # customer that pays our invoices

S = "sanne@studioklei.example"


def fmt_nl(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


MESSAGES = [
    ("01-herinnering-karton.eml", "Kartonfabriek Noord BV", "debiteuren@kartonfabriek-noord.example",
     "Betalingsherinnering factuur F-2026-0412", "2026-10-20",
     f"Beste Sanne,\n\nVolgens onze administratie staat factuur F-2026-0412 van 22 september nog open.\n"
     f"Openstaand bedrag: € 312,40.\nWilt u dit binnen 14 dagen overmaken naar {fmt_nl(KARTON)} t.n.v. "
     f"Kartonfabriek Noord BV, onder vermelding van het factuurnummer?\n\nMet vriendelijke groet,\nAfdeling debiteuren",
     "pay_invoice", "ok"),
    ("02-herinnering-glazuur-al-betaald.eml", "Glazuurhandel De Vries", "info@glazuurhandel-devries.example",
     "Herinnering: factuur GH-7781", "2026-10-18",
     f"Hallo,\n\nWe hebben de betaling van factuur GH-7781 nog niet ontvangen. Het bedrag is € 96,80.\n"
     f"Graag overmaken naar {GLAZUUR}.\n\nGroeten, Pieter de Vries", "pay_invoice", "blocked"),
    ("03-nota-boekhouder.eml", "Administratiekantoor Jansen", "nota@jansen-administratie.example",
     "Nota oktober 2026", "2026-10-21",
     f"Beste Sanne,\n\nHierbij de nota 2026-10 voor de maandelijkse administratie.\nTotaal te betalen: € 121,00 "
     f"(incl. btw).\nIBAN: {fmt_nl(BOEKHOUDER)}\n\nGroet, Marieke Jansen", "pay_invoice", "ok"),
    ("04-nieuwe-iban-fraude.eml", "Kartonfabriek Noord BV", "administratie@kartonfabriek-noord-bv.example",
     "SPOED: gewijzigde bankgegevens + factuur F-2026-0419", "2026-10-22",
     f"Beste klant,\n\nWegens een bankwissel is ons rekeningnummer gewijzigd. Betaal voortaan op {fmt_nl(FRAUD)} "
     f"t.n.v. Kartonfabriek Noord.\nOpenstaand bedrag factuur F-2026-0419: € 1.284,00.\nGraag vandaag nog, anders "
     f"stopt de levering. U hoeft niet te bellen, de afdeling is deze week onbereikbaar.\n\nMet vriendelijke groet,\n"
     f"Administratie", "pay_invoice", "blocked"),
    ("05-ceo-fraude.eml", "Sanne (privé)", "sanne.studioklei@freemail.example",
     "Even snel regelen", "2026-10-22",
     f"Hoi, ik zit in een vergadering en kan niet bellen. Wil je vandaag nog € 4.900,00 overmaken naar "
     f"{fmt_nl(CEO_FRAUD)} voor de nieuwe ovenleverancier? Het is vertrouwelijk, leg ik later uit. Betaal de "
     f"factuur 2026-55 meteen.", "pay_invoice", "blocked"),
    ("06-klant-terugbetaling.eml", "Lena Bakker", "lena@example.com", "Kapotte mok, bestelling 1042",
     "2026-10-19",
     "Hallo,\n\nMijn bestelling 1042 kwam vandaag aan, maar de mok was gebroken (foto bijgevoegd).\n"
     "Kunt u de € 38,00 terugbetalen?\n\nGroetjes, Lena", "refund", "ok"),
    ("07-refund-te-hoog.eml", "Tom Visser", "tom@example.com", "Refund order 1043", "2026-10-20",
     "Hi, I want to return order 1043. I paid 150.00 EUR for it, please refund the full 150.00 EUR. Thanks, Tom",
     "refund", "blocked"),
    ("08-fotograaf-fee.eml", "Ana Ribeiro", "ana.photo@example.com", "Product photos done", "2026-10-21",
     "Hello Sanne, the autumn product shoot is done and the photos are in the shared folder. My fee is "
     "180.00 EUR as agreed. Please pay to ana.photo@example.com via PayPal. Best, Ana", "payout", "ok"),
    ("09-onbekende-freelancer.eml", "Quick Design", "hello@quickdesign.example", "Invoice logo work",
     "2026-10-21",
     "Hi! Logo refresh delivered. My fee is 350.00 EUR, please pay to quick.design@example.net via PayPal today.",
     "payout", "blocked"),
    ("10-openstaand-cafe-blauw.eml", "Boekhoudpakket", "noreply@boekhoud.example",
     "Openstaande facturen", "2026-10-22",
     "Overzicht openstaande facturen:\nFactuur 2026-031 aan Café Blauw (info@cafeblauw.example), € 450,00, "
     "vervallen op 10-10-2026.\nStuur een herinnering aan de klant.", "send_reminder", "ok"),
    ("11-openstaand-al-betaald.eml", "Boekhoudpakket", "noreply@boekhoud.example",
     "Openstaande facturen (2)", "2026-10-22",
     "Overzicht openstaande facturen:\nFactuur 2026-029 aan Café Blauw (info@cafeblauw.example), € 275,00, "
     "vervallen op 01-10-2026.\nStuur een herinnering aan de klant.", "send_reminder", "blocked"),
    ("12-nieuwsbrief.eml", "Ondernemersnieuws", "nieuws@ondernemers.example", "Nieuwsbrief oktober",
     "2026-10-15",
     "In deze nieuwsbrief: vijf tips voor je eindejaarsactie, nieuwe regels voor webwinkels en een uitnodiging "
     "voor de netwerkborrel in Utrecht.", "no_action", "none"),
    ("13-phishing-bank.eml", "ING Zakelijk", "beveiliging@ing-zakelijk-verificatie.example",
     "Uw rekening wordt geblokkeerd", "2026-10-21",
     "Geachte klant,\n\nWij hebben verdachte activiteit gezien. Uw rekening wordt binnen 24 uur geblokkeerd "
     "als u uw gegevens niet bevestigt.\nKlik hier om te verifiëren: http://ing-zakelijk-verificatie.example/login\n",
     "flag_suspicious", "flagged"),
    ("14-nep-belasting.eml", "Belastingdienst", "aanslag@belasting-dienst-nl.example",
     "Laatste waarschuwing: openstaande aanslag", "2026-10-22",
     f"Laatste waarschuwing. U heeft een openstaande aanslag met kenmerk 2026-88123. Te betalen: € 1.250,00 "
     f"binnen 24 uur op {fmt_nl(BROKEN)}, anders volgt een deurwaarder.", "pay_invoice", "blocked"),
    ("15-energie-incasso.eml", "Stroom & Co", "factuur@stroomenco.example", "Uw termijnbedrag november",
     "2026-10-20",
     "Beste klant, uw termijnbedrag voor november is € 84,50. Dit bedrag wordt op of rond 28 oktober automatisch "
     "afgeschreven van uw rekening.", "no_action", "none"),
    ("16-dubbele-factuur.eml", "Kartonfabriek Noord BV", "debiteuren@kartonfabriek-noord.example",
     "Nogmaals: factuur F-2026-0412", "2026-10-23",
     f"Beste Sanne,\n\nNogmaals factuur F-2026-0412. Openstaand bedrag: € 312,40.\nGraag overmaken naar "
     f"{fmt_nl(KARTON)}.\n\nMet vriendelijke groet,\nAfdeling debiteuren", "pay_invoice", "blocked"),
    ("17-nep-paypal.eml", "PayPal", "service@paypa1-secure.example", "You received a payment",
     "2026-10-22",
     "You received a payment of 620.00 EUR from buyer Mark. The payment is on hold until you ship the item and add "
     "tracking. Click the link below to release the funds.", "flag_suspicious", "flagged"),
    ("18-klant-wil-factuur.eml", "Hotel aan Zee", "inkoop@hotelzee.example", "Bestelling espressokopjes",
     "2026-10-21",
     "Goedemiddag,\n\nKunnen jullie ons een factuur sturen voor 24 espressokopjes à € 18,00, totaal € 432,00? "
     "Graag naar inkoop@hotelzee.example.\n\nMet vriendelijke groet, Hotel aan Zee", "send_invoice", "ok"),
    ("19-marktplaats-truc.eml", "Koper123", "koper123@freemail.example", "Uw advertentie draaischijf",
     "2026-10-22",
     "Hallo, ik wil uw draaischijf kopen voor € 250,00. Betaal eerst € 0,01 via deze link om uw rekening te "
     "verifiëren, dan maak ik het geld over.", "flag_suspicious", "flagged"),
    ("20-grote-factuur-glazuur.eml", "Glazuurhandel De Vries", "info@glazuurhandel-devries.example",
     "Factuur GH-7802", "2026-10-23",
     f"Hallo Sanne,\n\nBijgaand factuur GH-7802 voor de jaarvoorraad glazuur. Totaal: € 1.850,00.\n"
     f"Graag overmaken naar {GLAZUUR} binnen 30 dagen.\n\nGroeten, Pieter", "pay_invoice", "blocked"),
    ("21-vraag-verzending.eml", "Jef Peeters", "jef@example.be", "Do you ship to Belgium?", "2026-10-23",
     "Hi! Do you ship to Belgium, and how long does it take? Love the blue mugs.", "no_action", "none"),
]

EXPECTED = {m[0]: (m[6], m[7]) for m in MESSAGES}

PAYPAL_HEADER = ["Date", "Time", "TimeZone", "Name", "Type", "Status", "Currency", "Gross", "Fee", "Net",
                 "From Email Address", "To Email Address", "Transaction ID", "Shipping Address", "Address Status",
                 "Item Title", "Item ID", "Shipping and Handling Amount", "Insurance Amount", "Sales Tax",
                 "Option 1 Name", "Option 1 Value", "Option 2 Name", "Option 2 Value", "Reference Txn ID",
                 "Invoice Number", "Custom Number", "Quantity", "Receipt ID", "Balance", "Address Line 1",
                 "Address Line 2/District/Neighborhood", "Town/City", "State/Province/Region/County/Territory/Prefecture/Republic",
                 "Zip/Postal Code", "Country", "Contact Phone Number", "Subject", "Note", "Country Code",
                 "Balance Impact"]
PAYPAL_ROWS = [  # date, name, type, gross, from, to, txid, item, invoice
    ("14/10/2026", "Lena Bakker", "Website Payment", "38.00", "lena@example.com", S, "1AB23456CD789012E",
     "Mok blauw", "1042"),
    ("15/10/2026", "Tom Visser", "Website Payment", "89.00", "tom@example.com", S, "2BC34567DE890123F",
     "Schaal groot", "1043"),
    ("30/09/2026", "Ana Ribeiro", "Mass Pay Payment", "-180.00", S, "ana.photo@example.com", "3CD45678EF901234G",
     "", "Shoot zomer"),
    ("31/08/2026", "Ana Ribeiro", "Mass Pay Payment", "-150.00", S, "ana.photo@example.com", "4DE56789FG012345H",
     "", "Shoot nieuwe collectie"),
]

ING_HEADER = ["Datum", "Naam / Omschrijving", "Rekening", "Tegenrekening", "Code", "Af Bij", "Bedrag (EUR)",
              "Mutatiesoort", "Mededelingen", "Saldo na mutatie", "Tag"]
ING_ROWS = [
    ("20260725", "Kartonfabriek Noord BV", KARTON, "GT", "Af", "287,15", "Online bankieren",
     f"Naam: Kartonfabriek Noord BV Omschrijving: F-2026-0301 IBAN: {KARTON} Valutadatum: 25-07-2026"),
    ("20260826", "Kartonfabriek Noord BV", KARTON, "GT", "Af", "301,90", "Online bankieren",
     f"Naam: Kartonfabriek Noord BV Omschrijving: F-2026-0355 IBAN: {KARTON} Valutadatum: 26-08-2026"),
    ("20260928", "Stroom & Co", make_nl("INGB", "0007777777"), "IC", "Af", "84,50", "Incasso",
     "Naam: Stroom & Co Omschrijving: Termijn oktober Machtiging ID: SC-1234"),
    ("20261002", "Lena Bakker", make_nl("SNSB", "0912345678"), "OV", "Bij", "24,00", "Overschrijving",
     "Naam: Lena Bakker Omschrijving: Bestelling 1011 IBAN: x"),
]

RABO_HEADER = ["IBAN/BBAN", "Munt", "BIC", "Volgnr", "Datum", "Rentedatum", "Bedrag", "Saldo na trn",
               "Tegenrekening IBAN/BBAN", "Naam tegenpartij", "Naam uiteindelijke partij", "Naam initiërende partij",
               "BIC tegenpartij", "Code", "Batch ID", "Transactiereferentie", "Machtigingskenmerk", "Incassant ID",
               "Betalingskenmerk", "Omschrijving-1", "Omschrijving-2", "Omschrijving-3", "Reden retour",
               "Oorspr bedrag", "Oorspr munt", "Koers"]
RABO_ROWS = [  # date, amount, counter iban, name, omschrijving
    ("2026-09-03", "-121,00", BOEKHOUDER, "Administratiekantoor Jansen", "Nota 2026-08"),
    ("2026-10-02", "-121,00", BOEKHOUDER, "Administratiekantoor Jansen", "Nota 2026-09"),
    ("2026-10-05", "+275,00", CAFE, "Cafe Blauw", "Factuur 2026-029"),
    ("2026-09-20", "-96,80", GLAZUUR, "Glazuurhandel De Vries", "Factuur GH-7781"),
]

ABN_ROWS = [  # date, start, end, amount, description
    ("20260910", "1520,10", "1406,60", "-113,50",
     f"/TRTP/SEPA OVERBOEKING/IBAN/{GLAZUUR}/BIC/RABONL2U/NAME/Glazuurhandel De Vries/REMI/Factuur GH-7702/EREF/NOTPROVIDED"),
    ("20260915", "1406,60", "1846,60", "+440,00",
     f"SEPA Overboeking                 IBAN: {CAFE}        BIC: RABONL2U         Naam: Cafe Blauw   Omschrijving: Factuur 2026-027"),
]


def _eml(fname, name, addr, subject, day, body) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{name} <{addr}>"
    msg["To"] = f"Sanne <{S}>"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=timezone.utc))
    msg["Message-ID"] = f"<{fname.removesuffix('.eml')}@sample.example>"
    msg.set_content(body)
    return msg


def write_samples(folder: str | Path) -> Path:
    root = Path(folder)
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    box_path = root / "inbox.mbox"
    if box_path.exists():
        box_path.unlink()
    box = mailbox.mbox(str(box_path))
    for fname, name, addr, subject, day, body, *_ in MESSAGES:
        msg = _eml(fname, name, addr, subject, day, body)
        (inbox / fname).write_bytes(bytes(msg))
        box.add(msg)
    box.flush()
    box.close()

    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(PAYPAL_HEADER)
    for d, name, typ, gross, frm, to, txid, item, inv in PAYPAL_ROWS:
        row = dict.fromkeys(PAYPAL_HEADER, "")
        row.update({"Date": d, "Time": "10:00:00", "TimeZone": "CEST", "Name": name, "Type": typ,
                    "Status": "Completed", "Currency": "EUR", "Gross": gross, "Fee": "0.00", "Net": gross,
                    "From Email Address": frm, "To Email Address": to, "Transaction ID": txid,
                    "Item Title": item, "Invoice Number": inv, "Balance Impact": "Credit" if gross[0] != "-" else "Debit"})
        w.writerow([row[h] for h in PAYPAL_HEADER])
    (root / "paypal.csv").write_text(buf.getvalue(), encoding="utf-8")

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(ING_HEADER)
    for d, name, tegen, code, afbij, amt, soort, med in ING_ROWS:
        w.writerow([d, name, OWN, tegen, code, afbij, amt, soort, med, "1000,00", ""])
    (root / "ing.csv").write_text(buf.getvalue(), encoding="utf-8")

    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(RABO_HEADER)
    for i, (d, amt, tegen, name, oms) in enumerate(RABO_ROWS, 1):
        row = dict.fromkeys(RABO_HEADER, "")
        row.update({"IBAN/BBAN": make_nl("RABO", "0300000001"), "Munt": "EUR", "BIC": "RABONL2U",
                    "Volgnr": f"{i:018d}", "Datum": d, "Rentedatum": d, "Bedrag": amt, "Saldo na trn": "+1000,00",
                    "Tegenrekening IBAN/BBAN": tegen, "Naam tegenpartij": name, "Code": "cb",
                    "Omschrijving-1": oms})
        w.writerow([row[h] for h in RABO_HEADER])
    (root / "rabobank.csv").write_text(buf.getvalue(), encoding="cp1252")

    lines = [f"456123000\tEUR\t{d}\t{s}\t{e}\t{d}\t{a}\t{desc}" for d, s, e, a, desc in ABN_ROWS]
    (root / "abnamro.txt").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return root
