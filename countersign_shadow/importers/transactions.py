# SPDX-License-Identifier: Apache-2.0
"""import-transactions: PayPal activity CSV and Dutch bank exports -> one shape.

Normalised transaction:
    {id, source, date (YYYY-MM-DD), amount (signed decimal string, + = money in), currency,
     counterparty, iban, email, description, reference, status, own_iban}

`own_iban` is the user's OWN account the row was booked on, where the export says so (ING "Rekening",
Rabobank "IBAN/BBAN", ASN/SNS/RegioBank "Je rekening", ABN AMRO account column, CAMT.053 Stmt/Acct, MT940 :25:);
"" when the export has none (PayPal) or it is not a valid IBAN. The checks treat it as known: an IBAN that is your
own account is never "an IBAN you never paid".

Layouts (see README "Bank formats" + docs/FORMATS.md for what was confirmed against which source):
  * Rabobank CSV (Rabobank "Formaatbeschrijving CSV" v1.2, April 2022): 26 quoted, comma-separated fields,
    header row, date EEJJ-MM-DD, amount with +/- sign and decimal comma.
  * ING CSV: header "Datum","Naam / Omschrijving","Rekening","Tegenrekening","Code","Af Bij","Bedrag (EUR)",
    "Mutatiesoort","Mededelingen" (+ "Saldo na mutatie","Tag" in the newer semicolon version).
  * ABN AMRO TXT: tab-separated, no header, 8 columns (account, currency, date, 2 balances, value date,
    amount, description) OR 10 columns (... amount, description, opposing name, opposing IBAN). The index
    positions of date/currency/amount/description hold in both; the two balance/date orders seen in the wild
    are both accepted.
  * PayPal "Activity download" CSV (English headers: Date, Time, Name, Type, Status, Currency, Gross, Fee,
    Net, From Email Address, To Email Address, Transaction ID, ... Invoice Number ...; Dutch headers confirmed
    against SnelStart's help page, see docs/FORMATS.md).
  * CAMT.053 (ISO 20022 XML "Bank to Customer Statement"): the bank-neutral structured export offered by
    ~every NL bank (incl. ING/Rabo/ABN business accounts, bunq, Knab, Triodos, ASN/SNS/RegioBank). Parsed by
    local XML tag name only (namespace- and schema-version-agnostic), see docs/FORMATS.md.
  * MT940 (SWIFT "Customer Statement Message"): the older structured export, still common in boekhoud-
    exports from Knab, Triodos, bunq and others. `:61:`/`:86:` transaction+narrative pairs.
  * ASN / SNS / RegioBank CSV ("de Volksbank" brands; SNS and RegioBank folded into the ASN Bank brand on
    2025-07-01): header row confirmed against RegioBank's own help page, see docs/FORMATS.md.

Every format now fails loudly with a Dutch message naming the missing column if the amount column cannot be
found, instead of silently importing zero transactions.

Robustness contract (after the importer fuzz): an importer never crashes on a damaged, truncated or
oddly-saved file. A row it cannot read is skipped and reported as one Dutch warning per file (appended to the
`warnings` list passed to import_file) that names the line numbers and any IBAN found on the skipped rows,
so a payee IBAN is never dropped without the user being told. A file that cannot be read at all raises one
friendly ImportError_ (Dutch). An unexpected internal error is turned into ImportCrash (an ImportError_ with a
friendly Dutch text) so the pilot app shows a message instead of a traceback; the fuzz harness treats
ImportCrash as a bug.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..iban import find_ibans, is_valid, normalise
from ..money import CENT, MoneyError, parse_signed

FORMATS = ("paypal", "ing", "rabobank", "abnamro", "camt053", "mt940", "volksbank")


class ImportError_(ValueError):
    pass


class ImportCrash(ImportError_):
    """An unexpected internal error while reading a file, shown to the user as one friendly Dutch error.
    Its __cause__ is the original exception; the fuzz harness reports that as a bug."""


MAX_SKIP_LINES_SHOWN = 8


class _Skips:
    """Collects rows a parser had to skip and turns them into ONE Dutch warning per file."""

    def __init__(self, bank: str, sink: list[str] | None):
        self.bank, self.sink, self.items = bank, sink, []  # items: (line number or 0, reason, [IBANs])

    def add(self, line: int, reason: str, raw: str = "") -> None:
        ibans = [i for i in find_ibans(raw or "") if is_valid(i)]
        self.items.append((line, reason, ibans))

    def flush(self) -> None:
        if self.items and self.sink is not None:
            self.sink.append(self.summary())

    def summary(self) -> str:
        n = len(self.items)
        lines = [str(ln) for ln, _r, _i in self.items if ln][:MAX_SKIP_LINES_SHOWN]
        more = n - len(lines) if lines else 0
        where = (f" (regel {', '.join(lines)}{f' en nog {more}' if more > 0 else ''})" if lines else "")
        reasons = list(dict.fromkeys(r for _l, r, _i in self.items))
        ibans = list(dict.fromkeys(i for _l, _r, ib in self.items for i in ib))
        msg = (f"{self.bank}-bestand: {n} regel{'s' if n != 1 else ''} overgeslagen{where}: "
               f"{'; '.join(reasons[:3])}. Controleer deze regel{'s' if n != 1 else ''} in je bankexport.")
        if ibans:
            msg += (f" LET OP: op de overgeslagen regel{'s' if n != 1 else ''} staat IBAN {', '.join(ibans[:10])}; "
                    "betalingen naar dit rekeningnummer zitten dus niet in de geschiedenis.")
        return msg


def _dec(s: str) -> Decimal:
    """A plain decimal amount ('123.45', bank XML style) as Decimal to the cent; MoneyError if unusable."""
    try:
        d = Decimal((s or "").strip())
        if not d.is_finite():
            raise MoneyError(f"not a finite amount: {s[:40]!r}")
        return d.quantize(CENT)
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError(f"not an amount: {(s or '')[:40]!r}") from exc


def _read_text(path: Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def _date(s: str, dayfirst: bool = True) -> str:
    s = (s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        d, mo = (a, b) if dayfirst else (b, a)
        if mo > 12:  # the guess was wrong; swap
            d, mo = mo, d
        return f"{y}-{mo:02d}-{d:02d}"
    return s


def _tx(source: str, date: str, amount: Decimal, currency: str, counterparty: str = "", iban: str = "",
        email: str = "", description: str = "", reference: str = "", status: str = "", raw_id: str = "",
        own_iban: str = "") -> dict:
    key = raw_id or f"{source}|{date}|{amount}|{counterparty}|{iban}|{description}"  # own_iban not in the id: re-import dedupes
    own = next((i for i in find_ibans(own_iban or "") if is_valid(i)), "")
    return {"id": "t-" + hashlib.sha256(key.encode()).hexdigest()[:10], "source": source, "date": date,
            "amount": str(amount), "currency": (currency or "EUR").upper(), "counterparty": counterparty.strip(),
            "iban": normalise(iban), "email": (email or "").strip().lower(),
            "description": " ".join((description or "").split()), "reference": (reference or "").strip(),
            "status": status, "own_iban": own}


def _rows(text: str, bank: str, delimiter: str | None = None) -> tuple[list[tuple[int, dict]], list[str]]:
    """CSV rows as (line number, row dict). Old-Mac / mixed line endings are normalised first (a stray '\r'
    inside an unquoted field used to crash the csv module); a file the csv module still cannot read becomes
    one friendly Dutch ImportError_."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    first = text.splitlines()[0] if text else ""
    if delimiter is None:
        delimiter = max([",", ";", "\t"], key=first.count)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = []
    try:
        for row in reader:
            rows.append((reader.line_num, row))
    except csv.Error as exc:
        raise ImportError_(f"{bank}-bestand: het bestand is beschadigd of afgekapt en kon niet gelezen worden "
                           f"(rond regel {reader.line_num}: {exc}). Download de export opnieuw bij je bank.") from exc
    fields = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    return rows, fields


def _csv_rows(text: str, bank: str, skips: _Skips, delimiter: str | None = None):
    """Yield (line, normalised row) for every CSV data row that can be used. Blank rows are ignored; a row with
    more filled-in columns than the header row is skipped with a warning, because its columns may have
    shifted (e.g. an unquoted comma in a name) and an amount read from it could be wrong."""
    rows, fields = _rows(text, bank, delimiter)
    out = []
    for line, row in rows:
        extra = [x for x in (row.get(None) or []) if (x or "").strip()]
        r = _norm_keys(row)
        if not any(r.values()) and not extra:
            continue
        if extra:
            skips.add(line, "meer kolommen dan de kopregel (verschoven kolommen?)",
                      " ".join(list(r.values()) + extra))
            continue
        out.append((line, r))
    return out, fields


def _require_column(fields: list[str], aliases: list[str], bank: str, label: str) -> None:
    """Fail loudly, in Dutch, naming the missing column -- instead of silently dropping every row."""
    if not any(a in fields for a in aliases):
        names = " / ".join(f"'{a}'" for a in aliases)
        raise ImportError_(f"{bank}-bestand: kolom {names} ({label}) niet gevonden; dit exportformaat "
                           f"wordt niet herkend (zie README 'Bank formats').")


def _require_nonempty(out: list, rows: list, bank: str, skips: "_Skips | None" = None) -> None:
    """Safety net: if every row was parsed but none produced a transaction, that is a silent-drop bug,
    not an empty statement -- fail loudly in Dutch rather than importing zero transactions."""
    if skips is not None and skips.items and not out:
        raise ImportError_(f"{bank}-bestand: geen enkele regel kon worden gelezen; het bestand is beschadigd of "
                           f"geen gewone export. {skips.summary()}")
    if rows and not out:
        raise ImportError_(f"{bank}-bestand: geen enkele regel kon worden gelezen; dit exportformaat "
                           f"wordt niet herkend (zie README 'Bank formats').")


def _norm_keys(row: dict) -> dict:
    # csv.DictReader puts surplus columns under the key None as a list: never a column of ours (handled by
    # _csv_rows); a short row gets None values.
    return {k.strip().lower(): (v or "").strip() for k, v in row.items() if isinstance(k, str) and not isinstance(v, list)}


# --------------------------------------------------------------------------------------------- PayPal
# Dutch headers confirmed 2026-10-24 against SnelStart's PayPal-import help page, which lists the exact 19
# Dutch field names PayPal's "Activiteitendownload" uses: Datum, Tijd, Tijdzone, Naam, Type, Status, Valuta,
# Bruto, Kosten, Net, Van e-mailadres, Naar e-mailadres, Transactiereferentie, BTW, Reference TxnID,
# Factuurnummer, Ontvangstbewijsreferentie, Saldo, Onderwerp.
# https://kennispleinweb.snelstart.nl/nl/paypal (checked 2026-10-24)
# "Type" and "Status" are the same word in both languages -- the earlier "beschrijving" guess for "type" was
# wrong (not a real PayPal header) and has been removed. "note"/"notitie" and "item title"/"objecttitel" are
# NOT in that confirmed 19-field list -- they remain unconfirmed guesses (see docs/FORMATS.md); missing them
# only loses optional description text, never a dropped transaction, so they stay best-effort.
PAYPAL_ALIASES = {
    "date": ["date", "datum"], "name": ["name", "naam"], "type": ["type"],
    "status": ["status"], "currency": ["currency", "valuta"], "gross": ["gross", "bruto"],
    "from": ["from email address", "van e-mailadres"], "to": ["to email address", "naar e-mailadres"],
    "txid": ["transaction id", "transactiereferentie"], "invoice": ["invoice number", "factuurnummer"],
    "subject": ["subject", "onderwerp"], "note": ["note", "notitie"], "item": ["item title", "objecttitel"],
}


def _pick(row: dict, field: str) -> str:
    for k in PAYPAL_ALIASES[field]:
        if k in row:
            return row[k]
    return ""


def parse_paypal(text: str, warnings: list[str] | None = None) -> list[dict]:
    skips = _Skips("PayPal", warnings)
    rows, fields = _csv_rows(text, "PayPal", skips)
    _require_column(fields, PAYPAL_ALIASES["gross"], "PayPal", "bedrag")
    out = []
    for line, r in rows:
        try:
            amt = parse_signed(_pick(r, "gross"))
        except MoneyError:
            skips.add(line, "bedrag niet leesbaar", " ".join(r.values()))
            continue
        email = _pick(r, "from") if amt > 0 else _pick(r, "to")
        desc = " ".join(x for x in (_pick(r, "type"), _pick(r, "item"), _pick(r, "subject"), _pick(r, "note")) if x)
        out.append(_tx("paypal", _date(_pick(r, "date")), amt, _pick(r, "currency"), _pick(r, "name"), "", email,
                       desc, _pick(r, "invoice"), _pick(r, "status"), "paypal|" + _pick(r, "txid")
                       if _pick(r, "txid") else ""))
    _require_nonempty(out, rows, "PayPal", skips)
    skips.flush()
    return out


# --------------------------------------------------------------------------------------------- ING
def parse_ing(text: str, warnings: list[str] | None = None) -> list[dict]:
    skips = _Skips("ING", warnings)
    rows, fields = _csv_rows(text, "ING", skips)
    if not any(f.startswith("bedrag") for f in fields):
        raise ImportError_("ING-bestand: kolom 'Bedrag (EUR)' niet gevonden; dit exportformaat wordt niet "
                           "herkend (zie README 'Bank formats').")
    out = []
    for line, r in rows:
        amount_key = next((k for k in r if k.startswith("bedrag")), "bedrag (eur)")
        try:
            amt = parse_signed(r.get(amount_key, ""))
        except MoneyError:
            skips.add(line, "bedrag niet leesbaar", " ".join(r.values()))
            continue
        if r.get("af bij", "").lower().startswith("af"):
            amt = -abs(amt)
        desc = r.get("mededelingen", "")
        ref = ""
        m = re.search(r"(?:Omschrijving|Kenmerk):\s*(.+?)(?=\s+(?:IBAN|Kenmerk|Valutadatum|Machtiging ID):|$)", desc)
        if m:
            ref = m.group(1).strip()
        out.append(_tx("ing", _date(r.get("datum", "")), amt, "EUR", r.get("naam / omschrijving", ""),
                       r.get("tegenrekening", ""), "", desc, ref, r.get("mutatiesoort", ""),
                       own_iban=r.get("rekening", "")))
    _require_nonempty(out, rows, "ING", skips)
    skips.flush()
    return out


# --------------------------------------------------------------------------------------------- Rabobank
def parse_rabobank(text: str, warnings: list[str] | None = None) -> list[dict]:
    skips = _Skips("Rabobank", warnings)
    rows, fields = _csv_rows(text, "Rabobank", skips, ",")
    _require_column(fields, ["bedrag"], "Rabobank", "bedrag")
    out = []
    for line, r in rows:
        try:
            amt = parse_signed(r.get("bedrag", ""))
        except MoneyError:
            skips.add(line, "bedrag niet leesbaar", " ".join(r.values()))
            continue
        desc = " ".join(r.get(f"omschrijving-{i}", "") for i in (1, 2, 3))
        ref = r.get("betalingskenmerk") or r.get("transactiereferentie") or ""
        raw_id = f"rabo|{r.get('iban/bban')}|{r.get('volgnr')}" if r.get("volgnr") else ""
        out.append(_tx("rabobank", _date(r.get("datum", "")), amt, r.get("munt", "EUR"), r.get("naam tegenpartij", ""),
                       r.get("tegenrekening iban/bban", ""), "", desc, ref, r.get("code", ""), raw_id,
                       own_iban=r.get("iban/bban", "")))
    _require_nonempty(out, rows, "Rabobank", skips)
    skips.flush()
    return out


# --------------------------------------------------------------------------------------------- ABN AMRO
def parse_abn_description(desc: str) -> dict:
    """ABN AMRO packs counterparty data into the description, in two known styles."""
    d = {"iban": "", "name": "", "text": desc, "ref": ""}
    if "/TRTP/" in desc:
        parts = desc.split("/")
        kv = {}
        for i in range(1, len(parts) - 1, 2):
            kv[parts[i].strip().upper()] = parts[i + 1].strip()
        d.update(iban=kv.get("IBAN", ""), name=kv.get("NAME", ""), text=kv.get("REMI", desc), ref=kv.get("EREF", ""))
        return d
    labels = r"(IBAN|BIC|Naam|Omschrijving|Kenmerk|Machtiging|Incassant|Betalingskenm\.?)"
    for m in re.finditer(rf"{labels}:\s*(.*?)(?=\s+{labels}:|$)", desc):
        key, val = m.group(1).lower(), m.group(2).strip()
        if key == "iban":
            d["iban"] = val
        elif key == "naam":
            d["name"] = val
        elif key == "omschrijving":
            d["text"] = val
        elif key.startswith("kenmerk") or key.startswith("betalingskenm"):
            d["ref"] = val
    return d


def parse_abnamro(text: str, warnings: list[str] | None = None) -> list[dict]:
    skips = _Skips("ABN AMRO", warnings)
    out = []
    lines = [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if ln.strip()]
    if not lines:
        return out
    header = None
    if re.search(r"(?i)transactiedatum|transactiondate", lines[0][1]):
        header = [h.strip().lower() for h in re.split(r"[\t;]", lines[0][1])]
        lines = lines[1:]
        if not any(h in header for h in ("transactiebedrag", "amount", "bedrag")):
            raise ImportError_("ABN AMRO-bestand: bedragkolom niet gevonden; dit exportformaat wordt niet "
                               "herkend (zie README 'Bank formats').")
    opp_name, opp_iban = "", ""
    for lineno, ln in lines:
        f = [x.strip() for x in (ln.split("\t") if "\t" in ln else ln.split(";"))]
        if len(f) < 8:
            skips.add(lineno, "minder dan 8 kolommen", ln)
            continue
        if header:
            r = dict(zip(header, f))
            date = r.get("transactiedatum") or r.get("transactiondate") or ""
            amount = r.get("transactiebedrag") or r.get("amount") or r.get("bedrag") or ""
            desc, cur = r.get("omschrijving") or r.get("description") or "", r.get("muntsoort") or r.get("mutationcode") or "EUR"
            opp_name, opp_iban = r.get("tegenpartij") or r.get("opposing account name") or "", \
                r.get("tegenrekening iban") or r.get("opposing iban") or ""
            acct = r.get("rekeningnummer") or r.get("accountnumber") or r.get("rekening") or f[0]
        else:
            # Index positions 1/2/6/7 (currency/date/amount/description) hold in both known no-header layouts
            # (denilsonsa/abn-amro-statement-parser's 8-col XLS order and the firefly-iii import-configurations
            # 10-col "nl/abnamro/default.json" layout, which adds opposing name/IBAN as columns 9/10). Only the
            # description itself (column 8) is taken; extra columns are read by position, never appended to it.
            date, cur, amount, desc, acct = f[2], f[1], f[6], f[7], f[0]
            opp_name = f[8] if len(f) >= 9 else ""
            opp_iban = f[9] if len(f) >= 10 else ""
        try:
            amt = parse_signed(amount)
        except MoneyError:
            skips.add(lineno, "bedrag niet leesbaar", ln)
            continue
        p = parse_abn_description(desc)
        name, iban_val = p["name"] or opp_name, p["iban"] or opp_iban
        out.append(_tx("abnamro", _date(date), amt, cur, name, iban_val, "", desc, p["ref"], own_iban=acct))
    if not out:
        raise ImportError_("ABN AMRO-bestand: geen enkele regel kon worden gelezen (minder dan 8 kolommen of "
                           "geen leesbaar bedrag per regel); dit exportformaat wordt niet herkend (zie README "
                           "'Bank formats')." + (f" {skips.summary()}" if skips.items else ""))
    skips.flush()
    return out


# --------------------------------------------------------------------------------------------- CAMT.053 (ISO 20022 XML)
# Structure confirmed 2026-10-24 against a real camt.053.001.02 sample (jasperkrijgsman/dutch-sepa-iso20022,
# GitHub) and cross-checked against the element names used by multiple banks' developer docs (Goldman Sachs,
# Huntington, Nordea camt.053 guides -- the core Document/BkToCstmrStmt/Stmt/Ntry/NtryDtls/TxDtls/RltdPties
# structure is identical across issuers and schema point-versions, which is the whole point of ISO 20022):
#   Document/BkToCstmrStmt/Stmt/Ntry            one entry (one booked line): Amt[@Ccy], CdtDbtInd (CRDT/DBIT),
#                                                 BookgDt/Dt, NtryDtls/TxDtls (0 or more)
#   .../TxDtls/RltdPties/Dbtr/Nm, DbtrAcct/Id/IBAN      payer name + IBAN (the counterparty when CdtDbtInd=CRDT)
#   .../TxDtls/RltdPties/Cdtr/Nm, CdtrAcct/Id/IBAN      payee name + IBAN (the counterparty when CdtDbtInd=DBIT)
#   .../TxDtls/RmtInf/Ustrd                              free-text description
#   .../TxDtls/Refs/EndToEndId                           payment reference
# Namespace and exact schema suffix (.001.02 / .001.08 / ...) are not checked -- only local (non-namespaced)
# tag names are matched, since the core entry structure is stable across versions and this is the one format
# meant to work the same for every bank. A batch entry with no TxDtls (rare for a sole trader/MKB account)
# degrades to AddtlNtryInf for the description, with an empty counterparty, rather than crashing.
def _ltag(e) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _child(e, tag):
    if e is None:
        return None
    for c in e:
        if _ltag(c) == tag:
            return c
    return None


def _childtext(e, *tags) -> str:
    cur = e
    for t in tags:
        cur = _child(cur, t)
    return (cur.text or "").strip() if cur is not None and cur.text else ""


def _children(e, tag) -> list:
    return [c for c in (e if e is not None else []) if _ltag(c) == tag]


def parse_camt053(text: str, warnings: list[str] | None = None) -> list[dict]:
    skips = _Skips("CAMT.053", warnings)
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError) as exc:
        raise ImportError_(f"CAMT.053-bestand: kon de XML niet lezen ({exc}); dit exportformaat wordt niet "
                           f"herkend (zie README 'Bank formats').") from exc
    entries = [e for e in root.iter() if _ltag(e) == "Ntry"]
    own_of = {id(e): _childtext(st, "Acct", "Id", "IBAN") for st in root.iter() if _ltag(st) == "Stmt"
              for e in st.iter() if _ltag(e) == "Ntry"}
    if not entries:
        raise ImportError_("CAMT.053-bestand: geen 'Ntry' (boekingsregels) gevonden in de XML; dit "
                           "exportformaat wordt niet herkend (zie README 'Bank formats').")
    out = []
    for n, ntry in enumerate(entries, 1):
        amt_el = _child(ntry, "Amt")
        try:
            amt = _dec(amt_el.text if amt_el is not None else "")
        except MoneyError:
            skips.add(0, f"boeking {n} zonder leesbaar bedrag",
                      " ".join(t.strip() for t in ntry.itertext() if t and t.strip()))
            continue
        ccy = amt_el.get("Ccy", "EUR")
        is_debit = _childtext(ntry, "CdtDbtInd").upper() == "DBIT"
        if is_debit:
            amt = -amt
        date = (_childtext(ntry, "BookgDt", "Dt") or _childtext(ntry, "BookgDt", "DtTm") or
                _childtext(ntry, "ValDt", "Dt") or _childtext(ntry, "ValDt", "DtTm"))[:10]
        name, ibn, desc, ref = "", "", "", ""
        txdtls = [t for d in _children(ntry, "NtryDtls") for t in _children(d, "TxDtls")]
        if txdtls:
            txd = txdtls[0]
            rp = _child(txd, "RltdPties")
            if is_debit:
                name, ibn = _childtext(rp, "Cdtr", "Nm"), _childtext(rp, "CdtrAcct", "Id", "IBAN")
            else:
                name, ibn = _childtext(rp, "Dbtr", "Nm"), _childtext(rp, "DbtrAcct", "Id", "IBAN")
            ustrd = [(u.text or "").strip() for ri in _children(txd, "RmtInf") for u in _children(ri, "Ustrd")]
            desc = " ".join(x for x in ustrd if x)
            ref = _childtext(txd, "Refs", "EndToEndId")
        if not desc:
            desc = _childtext(ntry, "AddtlNtryInf")
        out.append(_tx("camt053", _date(date), amt, ccy, name, ibn, "", desc, ref, own_iban=own_of.get(id(ntry), "")))
    _require_nonempty(out, entries, "CAMT.053", skips)
    skips.flush()
    return out


# --------------------------------------------------------------------------------------------- MT940
# SWIFT "Customer Statement Message" (MT940): confirmed 2026-10-24 against the actively maintained
# `WoLpH/mt940` Python library's README (field formats: :61: = YYMMDD[entry-date]C|D|RC|RD<amount>...,
# amount with decimal comma; :86: = free-text narrative, continuation lines without a leading ':NN:' tag).
# No SWIFT block wrapper ("{1:F01...}") is required or produced by Dutch bank/accounting exports -- the
# plain tag stream (":20:" ... ":62F:") is what gets handed to a boekhoudpakket, and what this reads.
# The free-text :86: narrative often reuses the same structured "/TRTP/.../IBAN/.../NAME/.../REMI/" and
# "IBAN: ... Naam: ... Omschrijving: ..." styles already confirmed for ABN AMRO -- parse_abn_description()
# is reused here rather than duplicated.
_MT940_TAG_RE = re.compile(r"^:(\d{2}[A-Z]?):(.*)$")
_MT940_TX_RE = re.compile(r"^(\d{6})(?:\d{4})?(R?[CD])(\d+(?:,\d{1,2})?)")


def _mt940_tags(text: str) -> list[list]:
    """[tag, value, line number] per tag; continuation lines are appended to the previous tag's value."""
    tags: list[list] = []
    for i, ln in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        if not ln.strip():
            continue
        m = _MT940_TAG_RE.match(ln)
        if m:
            tags.append([m.group(1), m.group(2), i])
        elif tags:
            tags[-1][1] += " " + ln.strip()
    return tags


def parse_mt940(text: str, warnings: list[str] | None = None) -> list[dict]:
    skips = _Skips("MT940", warnings)
    tags = _mt940_tags(text)
    tx_lines = [v for t, v, _i in tags if t == "61"]
    if not tx_lines:
        raise ImportError_("MT940-bestand: geen ':61:'-transactieregels gevonden; dit exportformaat wordt "
                           "niet herkend (zie README 'Bank formats').")
    out = []
    acct = [""]  # the current statement's :25: account identification (e.g. "NL69INGB0123456789EUR")

    def flush(tx61: str, info86: str, lineno: int) -> None:
        m = _MT940_TX_RE.match(tx61)
        if not m:
            skips.add(lineno, "':61:'-regel niet leesbaar", f"{tx61} {info86}")
            return
        date_s, sign, amt_s = m.groups()
        try:
            amt = _dec(amt_s.replace(",", "."))
        except MoneyError:
            skips.add(lineno, "bedrag niet leesbaar", f"{tx61} {info86}")
            return
        if sign.endswith("D"):
            amt = -amt
        date = f"20{date_s[0:2]}-{date_s[2:4]}-{date_s[4:6]}"
        desc = info86.strip()
        p = parse_abn_description(desc) if desc else {"iban": "", "name": "", "text": "", "ref": ""}
        out.append(_tx("mt940", date, amt, "EUR", p["name"], p["iban"], "", p["text"] or desc, p["ref"],
                       own_iban=acct[0]))

    pending, pending_line = None, 0
    for tag, val, lineno in tags:
        if tag == "25":
            acct[0] = re.sub(r"(?i)^[A-Z0-9]{8,11}/", "", val.strip())  # "INGBNL2A/NL69..." -> "NL69..."
        if tag == "61":
            if pending is not None:
                flush(pending, "", pending_line)
            pending, pending_line = val, lineno
        elif tag == "86" and pending is not None:
            flush(pending, val, pending_line)
            pending = None
    if pending is not None:
        flush(pending, "", pending_line)
    _require_nonempty(out, tx_lines, "MT940", skips)
    skips.flush()
    return out


# --------------------------------------------------------------------------------------------- ASN / SNS / RegioBank ("de Volksbank")
# Header row confirmed 2026-10-24 against RegioBank's own help page listing every CSV column name
# (www.regiobank.nl/zakelijk/service/rekeningafschriften.html). ASN Bank, SNS and RegioBank are all brands of
# the same bank (de Volksbank) and share one banking platform; SNS and RegioBank were folded into the single
# "ASN Bank" brand on 2025-07-01 (confirmed via Yuki's bank-export-formats page). The shared-platform CSV
# layout is assumed to carry over to all three brand names; this has NOT been independently re-confirmed
# against ASN's or SNS's own (pre-merger) help pages, only RegioBank's. Delimiter, date format and decimal
# separator were not stated on that page either -- the importer therefore reuses the same delimiter
# autodetection and lenient date/amount parsing already used for every other CSV format here, rather than
# assuming one convention.
VOLKSBANK_AMOUNT_ALIASES = ["bedrag bij/af", "bedrag"]


def parse_volksbank(text: str, warnings: list[str] | None = None) -> list[dict]:
    skips = _Skips("ASN/SNS/RegioBank", warnings)
    rows, fields = _csv_rows(text, "ASN/SNS/RegioBank", skips)
    _require_column(fields, VOLKSBANK_AMOUNT_ALIASES, "ASN/SNS/RegioBank", "bedrag")
    out = []
    for line, r in rows:
        amount_key = next((k for k in VOLKSBANK_AMOUNT_ALIASES if k in r), None)
        try:
            amt = parse_signed(r.get(amount_key, ""))
        except MoneyError:
            skips.add(line, "bedrag niet leesbaar", " ".join(r.values()))
            continue
        out.append(_tx("volksbank", _date(r.get("datum", "")), amt, r.get("valuta boeking", "EUR"),
                       r.get("naam", ""), r.get("van / naar", ""), "", r.get("omschrijving", ""),
                       r.get("betalingskenmerk", ""), own_iban=r.get("je rekening", "")))
    _require_nonempty(out, rows, "ASN/SNS/RegioBank", skips)
    skips.flush()
    return out


PARSERS = {"paypal": parse_paypal, "ing": parse_ing, "rabobank": parse_rabobank, "abnamro": parse_abnamro,
           "camt053": parse_camt053, "mt940": parse_mt940, "volksbank": parse_volksbank}


def detect(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("<?xml") or stripped.startswith("<Document"):
        probe = text[:3000].lower()
        if "camt.053" in probe or "bktocstmrstmt" in probe:
            return "camt053"
        raise ImportError_("unknown transaction export: XML file does not look like a CAMT.053 bank "
                           "statement (use --format to force one)")
    if re.search(r"(?m)^:20:", text) and re.search(r"(?m)^:61:", text):
        return "mt940"
    first = (text.splitlines()[0] if text else "").lower()
    if "naam tegenpartij" in first and "iban/bban" in first:
        return "rabobank"
    if "naam / omschrijving" in first and "af bij" in first:
        return "ing"
    if ("gross" in first or "bruto" in first) and ("transaction id" in first or "transactiereferentie" in first):
        return "paypal"
    if "je rekening" in first and ("bedrag bij/af" in first or "van / naar" in first):
        return "volksbank"
    if "transactiedatum" in first or "transactiondate" in first:
        return "abnamro"
    if "\t" in first and re.match(r"^\S+\t[A-Z]{3}\t\d{8}\t", text.splitlines()[0]):
        return "abnamro"
    raise ImportError_("unknown transaction export: expected a PayPal activity CSV or an ING / Rabobank / "
                       "ABN AMRO / CAMT.053 / MT940 / ASN-SNS-RegioBank export (use --format to force one)")


def import_file(path: str | Path, fmt: str = "auto", warnings: list[str] | None = None) -> tuple[str, list[dict]]:
    """(format, transactions). Raises ImportError_ (Dutch text) if the file cannot be read at all; rows that
    had to be skipped are reported as Dutch text appended to `warnings` (pass a list, and show it)."""
    text = _read_text(Path(path))
    kind = detect(text) if fmt == "auto" else fmt
    if kind not in PARSERS:
        raise ImportError_(f"unknown format {fmt!r}; choose one of {', '.join(FORMATS)}")
    try:
        return kind, PARSERS[kind](text, warnings)
    except ImportError_:
        raise
    except Exception as exc:  # never a traceback for the pilot user; the fuzz harness reports __cause__
        raise ImportCrash(f"{Path(path).name}: dit bestand kon niet gelezen worden (onverwachte fout: "
                          f"{type(exc).__name__}). Is het beschadigd of afgekapt? Download de export opnieuw "
                          "bij je bank; lukt het dan nog niet, stuur ons dit bestand.") from exc
