# SPDX-License-Identifier: Apache-2.0
"""Structure-aware "valid file" builders: one per transaction-export format, plus PDF and .msg.

Each `build_*(draw)` returns (content: bytes, ground_truth: list[dict]). `ground_truth` rows carry
exactly the fields the oracle checks: amount (signed Decimal string, 2dp), date (YYYY-MM-DD), iban
(normalised, "" if the format never carries one), counterparty, email (paypal only).

Every format intentionally avoids "/" inside generated names/descriptions, because the ABN AMRO and
MT940 structured-narrative style ("/TRTP/.../IBAN/.../NAME/.../REMI/") that's reused to get a
deterministic counterparty+IBAN is itself "/"-delimited.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from hypothesis import strategies as st

from . import genlib as g

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, so "tests.fixtures" is importable
from tests.fixtures import make_msg, make_pdf  # noqa: E402  (fixture reuse: minimal PDF/.msg writers)


def _clean_name(draw) -> str:
    return draw(g.st_name()).replace("/", "-")


def _clean_desc(draw, max_len: int = 60) -> str:
    return draw(g.st_description(max_len)).replace("/", "-")


def _one_row_common(draw):
    cents = draw(g.st_amount_cents())
    sign = draw(st.booleans())  # True = money in (credit), False = money out (debit)
    cents = cents if sign else -cents
    return {
        "cents": cents,
        "date": draw(g.st_date()),
        "iban": draw(g.st_iban()),
        "name": _clean_name(draw),
        "desc": _clean_desc(draw),
    }


def _finalise(lines: list[str], newline: str, encoding: str) -> bytes:
    return g.encode_text(newline.join(lines) + newline, encoding)


def _ground_truth(row: dict, with_iban: bool = True, email: str = "") -> dict:
    return {
        "amount": g.cents_to_str(row["cents"]),
        "date": row["date"].isoformat(),
        "iban": row["iban"] if with_iban else "",
        "counterparty": row["name"],
        "email": email,
    }


# --------------------------------------------------------------------------------------------- PayPal
def build_paypal(draw) -> tuple[bytes, list[dict]]:
    n = draw(st.integers(1, 4))
    rows, truth = [], []
    for _ in range(n):
        r = _one_row_common(draw)
        email = f"user{draw(st.integers(0, 99999))}@example.com"
        frm = email if r["cents"] > 0 else "shop@studioklei.example"
        to = "shop@studioklei.example" if r["cents"] > 0 else email
        rows.append({"Date": r["date"].isoformat(), "Name": r["name"], "Type": "Payment", "Status": "Completed",
                     "Currency": "EUR", "Gross": g.cents_to_str(r["cents"]),
                     "From Email Address": frm, "To Email Address": to,
                     "Transaction ID": f"TX{draw(st.integers(10**9, 10**10 - 1))}", "Invoice Number": ""})
        truth.append(_ground_truth(r, with_iban=False, email=email))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator=draw(g.st_newline()))
    w.writeheader()
    w.writerows(rows)
    return g.encode_text(buf.getvalue(), draw(g.st_encoding())), truth


# --------------------------------------------------------------------------------------------- ING
def build_ing(draw) -> tuple[bytes, list[dict]]:
    n = draw(st.integers(1, 4))
    rows, truth = [], []
    for _ in range(n):
        r = _one_row_common(draw)
        af_bij = "Af" if r["cents"] < 0 else "Bij"
        rows.append({"Datum": r["date"].strftime("%Y%m%d"), "Naam / Omschrijving": r["name"],
                     "Rekening": draw(g.st_iban()), "Tegenrekening": r["iban"], "Code": "GT",
                     "Af Bij": af_bij, "Bedrag (EUR)": g.cents_to_str(abs(r["cents"]), ","),
                     "Mutatiesoort": "Overboeking", "Mededelingen": r["desc"]})
        truth.append(_ground_truth(r))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";", lineterminator=draw(g.st_newline()))
    w.writeheader()
    w.writerows(rows)
    return g.encode_text(buf.getvalue(), draw(g.st_encoding())), truth


# --------------------------------------------------------------------------------------------- Rabobank
def build_rabobank(draw) -> tuple[bytes, list[dict]]:
    n = draw(st.integers(1, 4))
    rows, truth = [], []
    for i in range(n):
        r = _one_row_common(draw)
        sign = "+" if r["cents"] >= 0 else "-"
        rows.append({"IBAN/BBAN": draw(g.st_iban()), "Munt": "EUR", "Volgnr": str(i + 1),
                     "Datum": r["date"].strftime("%Y%m%d"), "Bedrag": sign + g.cents_to_str(abs(r["cents"]), ","),
                     "Tegenrekening IBAN/BBAN": r["iban"], "Naam tegenpartij": r["name"], "Code": "GT",
                     "Betalingskenmerk": "", "Omschrijving-1": r["desc"], "Omschrijving-2": "",
                     "Omschrijving-3": ""})
        truth.append(_ground_truth(r))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator=draw(g.st_newline()))
    w.writeheader()
    w.writerows(rows)
    return g.encode_text(buf.getvalue(), draw(g.st_encoding())), truth


# --------------------------------------------------------------------------------------------- ABN AMRO
def build_abnamro(draw) -> tuple[bytes, list[dict]]:
    """8-column tab-separated layout, with the counterparty+IBAN carried in the structured
    '/TRTP/.../NAME/.../IBAN/.../REMI/' narrative (so it round-trips regardless of column count)."""
    n = draw(st.integers(1, 4))
    newline, enc = draw(g.st_newline()), draw(g.st_encoding())
    header = ["Transactiedatum", "Rekening", "Valutadatum", "Saldo", "SaldoNa", "Muntsoort",
              "Transactiebedrag", "Omschrijving"]
    lines = ["\t".join(header)]
    truth = []
    for _ in range(n):
        r = _one_row_common(draw)
        sign = "+" if r["cents"] >= 0 else "-"
        narrative = f"/TRTP/SEPA OVERBOEKING/IBAN/{r['iban']}/NAME/{r['name']}/REMI/{r['desc']}"
        row = [r["date"].strftime("%Y%m%d"), draw(g.st_iban()), r["date"].strftime("%Y%m%d"), "1000,00",
               "1000,00", "EUR", sign + g.cents_to_str(abs(r["cents"]), ","), narrative]
        lines.append("\t".join(row))
        truth.append(_ground_truth(r))
    return _finalise(lines, newline, enc), truth


# --------------------------------------------------------------------------------------------- CAMT.053
def build_camt053(draw) -> tuple[bytes, list[dict]]:
    n = draw(st.integers(1, 4))
    enc = draw(g.st_encoding())
    entries = []
    truth = []
    for _ in range(n):
        r = _one_row_common(draw)
        is_debit = r["cents"] < 0
        ind = "DBIT" if is_debit else "CRDT"
        party_tag, acct_tag = ("Cdtr", "CdtrAcct") if is_debit else ("Dbtr", "DbtrAcct")
        name_esc, desc_esc = escape(r["name"]), escape(r["desc"])
        entries.append(f"""
    <Ntry>
      <Amt Ccy="EUR">{g.cents_to_str(abs(r["cents"]))}</Amt>
      <CdtDbtInd>{ind}</CdtDbtInd>
      <BookgDt><Dt>{r["date"].isoformat()}</Dt></BookgDt>
      <NtryDtls>
        <TxDtls>
          <RltdPties>
            <{party_tag}><Nm>{name_esc}</Nm></{party_tag}>
            <{acct_tag}><Id><IBAN>{r["iban"]}</IBAN></Id></{acct_tag}>
          </RltdPties>
          <RmtInf><Ustrd>{desc_esc}</Ustrd></RmtInf>
          <Refs><EndToEndId>REF{draw(st.integers(1, 999999))}</EndToEndId></Refs>
        </TxDtls>
      </NtryDtls>
    </Ntry>""")
        truth.append(_ground_truth(r))
    xml = (f'<?xml version="1.0" encoding="{("UTF-8" if enc != "cp1252" else "windows-1252")}"?>\n'
           f'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
           f'<BkToCstmrStmt><Stmt>{"".join(entries)}\n  </Stmt></BkToCstmrStmt></Document>\n')
    return g.encode_text(xml, enc), truth


# --------------------------------------------------------------------------------------------- MT940
def build_mt940(draw) -> tuple[bytes, list[dict]]:
    n = draw(st.integers(1, 4))
    newline, enc = draw(g.st_newline()), draw(g.st_encoding())
    lines = [":20:REF" + str(draw(st.integers(1, 999999))), ":25:" + draw(g.st_iban()), ":28C:1/1",
             ":60F:C" + draw(g.st_date()).strftime("%y%m%d") + "EUR0,00"]
    truth = []
    for _ in range(n):
        r = _one_row_common(draw)
        cd = "C" if r["cents"] >= 0 else "D"
        lines.append(f":61:{r['date'].strftime('%y%m%d')}{cd}{g.cents_to_str(abs(r['cents']), ',')}NTRFNONREF")
        lines.append(f":86:/TRTP/SEPA/IBAN/{r['iban']}/NAME/{r['name']}/REMI/{r['desc']}")
        truth.append(_ground_truth(r))
    lines.append(":62F:C" + draw(g.st_date()).strftime("%y%m%d") + "EUR0,00")
    return _finalise(lines, newline, enc), truth


# --------------------------------------------------------------------------------------------- Volksbank
def build_volksbank(draw) -> tuple[bytes, list[dict]]:
    n = draw(st.integers(1, 4))
    header = ["Datum", "Je rekening", "Van / naar", "Naam", "Valuta saldo", "Saldo voor boeking",
              "Valuta boeking", "Bedrag bij/af", "Verwerkingsdatum", "Valutadatum", "Code", "Type",
              "Volgnummer", "Betalingskenmerk", "Omschrijving", "Afschriftnummer", "Categorie"]
    rows, truth = [], []
    for i in range(n):
        r = _one_row_common(draw)
        sign = "+" if r["cents"] >= 0 else "-"
        rows.append(dict(zip(header, [
            r["date"].strftime("%Y%m%d"), draw(g.st_iban()), r["iban"], r["name"], "EUR", "1000,00", "EUR",
            sign + g.cents_to_str(abs(r["cents"]), ","), r["date"].strftime("%Y%m%d"),
            r["date"].strftime("%Y%m%d"), "GT", "Overboeking", str(i + 1), "", r["desc"], str(i + 1), ""])))
        truth.append(_ground_truth(r))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator=draw(g.st_newline()))
    w.writeheader()
    w.writerows(rows)
    return g.encode_text(buf.getvalue(), draw(g.st_encoding())), truth


BUILDERS = {"paypal": build_paypal, "ing": build_ing, "rabobank": build_rabobank, "abnamro": build_abnamro,
            "camt053": build_camt053, "mt940": build_mt940, "volksbank": build_volksbank}


# --------------------------------------------------------------------------------------------- PDF / .msg
def build_pdf(draw) -> tuple[bytes, dict]:
    """A one-page text invoice PDF, reusing tests.fixtures' minimal PDF writer (no extra dependency)."""
    iban = draw(g.st_iban())
    name = _clean_name(draw)
    cents = draw(g.st_amount_cents())
    invoice_no = f"F-{draw(st.integers(1000, 9999))}"
    lines = [f"Factuur {invoice_no}", f"{name} B.V.", f"IBAN: {iban}",
             f"Totaal te betalen: EUR {g.cents_to_str(cents)}", _clean_desc(draw, 120)]
    return make_pdf(lines), {"iban": iban, "amount": g.cents_to_str(cents), "invoice_ref": invoice_no}


def build_msg(draw) -> tuple[bytes, dict]:
    """A minimal valid Outlook .msg (OLE compound file), reusing tests.fixtures' CFB writer."""
    from datetime import datetime, timezone

    subject = _clean_desc(draw, 60) or "Factuur"
    sender_name = _clean_name(draw)
    sender = f"user{draw(st.integers(0, 99999))}@example.com"
    body = _clean_desc(draw, 500)
    sent = datetime(2026, draw(st.integers(1, 12)), draw(st.integers(1, 28)), tzinfo=timezone.utc)
    return make_msg(subject, sender_name, sender, body, sent), {"subject": subject, "from": sender}
