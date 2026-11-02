# Bank/PayPal import formats — evidence log

Written to confirm or fix the four importers against real-world evidence instead of memory/guesses.
No real export file from any bank or from PayPal was available; everything below is from public, maintained
sources. All fixtures in `tests/test_shadow.py` and `samples/` are synthetic (fake names/IBANs), never real data.

## Later additions: CAMT.053, MT940, ASN/SNS/RegioBank CSV

Aim: pilot zzp/MKB users bank at bunq, Knab, Triodos, ASN/SNS/RegioBank too, and most accounting tools also
hand back CAMT.053 or MT940. No real export file was available for this check either.

| Format | Status | What's confirmed | Source(s) | Checked |
|---|---|---|---|---|
| CAMT.053 (ISO 20022 "Bank to Customer Statement" XML) | **Confirmed** | Core element structure `Document/BkToCstmrStmt/Stmt/Ntry` with `Amt`/`CdtDbtInd`/`BookgDt`, and per-transaction `NtryDtls/TxDtls/RltdPties/Dbtr`\|`Cdtr`/`Nm` + `DbtrAcct`\|`CdtrAcct/Id/IBAN` + `RmtInf/Ustrd`. This structure is bank-neutral by design (ISO 20022) and is offered by every NL bank this check looked at (ING, Rabo, ABN AMRO business accounts, bunq, Knab, Triodos, ASN, SNS, RegioBank — see Yuki's export-formats list below) | A real camt.053.001.02 sample file: `jasperkrijgsman/dutch-sepa-iso20022` (GitHub, `src/main/resources/camt.053.001.02.xml`); element names cross-checked against bank developer-portal camt.053 guides (Goldman Sachs, Huntington, Nordea — same core structure regardless of issuer or schema point-version) | 2026-10-24 |
| MT940 (SWIFT "Customer Statement Message") | **Confirmed** | `:61:` transaction-line format (`YYMMDD[entry-date]C\|D\|RC\|RD<amount,with,decimal,comma>...`) and `:86:` free-text narrative, paired 1:1 after each `:61:` | The actively maintained `WoLpH/mt940` Python library's README (real users parse real bank MT940 files with it) | 2026-10-24 |
| MT940 `:86:` narrative structured sub-fields (`/TRTP/.../IBAN/.../NAME/.../REMI/` and `IBAN: … Naam: … Omschrijving:` styles) | Reused, not independently re-confirmed for MT940 specifically | — | Same two styles already logged above as unconfirmed-from-memory for ABN AMRO; `parse_abn_description()` is reused as-is rather than duplicated, so it inherits that same confidence level | — |
| ASN Bank / SNS / RegioBank CSV ("de Volksbank" brands) | **Confirmed header row; delimiter/date/decimal format NOT independently confirmed** | The exact 17 Dutch column names, in order: `Datum, Je rekening, Van / naar, Naam, Valuta saldo, Saldo voor boeking, Valuta boeking, Bedrag bij/af, Verwerkingsdatum, Valutadatum, Code, Type, Volgnummer, Betalingskenmerk, Omschrijving, Afschriftnummer, Categorie` | RegioBank's own help page (www.regiobank.nl/zakelijk/service/rekeningafschriften.html), which spells out every column and what it means | 2026-10-24 |
| ASN/SNS CSV assumed identical to RegioBank's | Assumption, not independently confirmed | — | ASN Bank, SNS and RegioBank are three brand names on one shared banking platform (de Volksbank); SNS and RegioBank were folded into the single "ASN Bank" brand on 2025-07-01 (Yuki's bank-export-formats page, checked 2026-10-24). No ASN-specific or SNS-specific (pre-merger) help page with the exact CSV header was found this check | — |
| Knab, Triodos, bunq: bank-specific CSV export | **Not supported yet** — no public source gave the exact column header text | Knab's own CSV export is mentioned (bankconvert.nl, Acumulus/Siel's Knab page) but no literal header row was published anywhere found; bunq's doc.bunq.com, help.bunq.com and together.bunq.com pages confirm a CSV export exists but likewise never quote its literal headers; Triodos's own FAQ confirms CSV is one of several export formats but, again, without column names | multiple searches, 2026-10-24 | — |
| Knab, Triodos, bunq: covered instead via CAMT.053 / MT940 | **Confirmed as a practical workaround** | Yuki's "list of supported bank and online payment providers" page states Knab exports MT940 (primary) or CAMT.053, Triodos exports MT940 (unstructured) or CAMT.053, and bunq exports MT940 or CAMT.053 — all three are importable today via the two generic formats above, even without a bank-specific CSV importer | `support.yuki.nl` "List of supported export formats from banks and online payment providers" | 2026-10-24 |

### What changed in the importer (`countersign_shadow/importers/transactions.py`)

1. Added `parse_camt053` (ISO 20022 XML, stdlib `xml.etree.ElementTree`, namespace- and schema-version-agnostic
   via local-tag-name matching) and a `detect()` branch for `<?xml` / `<Document` content mentioning
   `camt.053` or `BkToCstmrStmt`.
2. Added `parse_mt940` (`:NN:` tag parsing, `:61:`/`:86:` pairing) and a `detect()` branch for text containing
   both a `:20:` and a `:61:` line. Reuses `parse_abn_description()` for the `:86:` narrative instead of a new
   parser.
3. Added `parse_volksbank` for the ASN/SNS/RegioBank CSV header above, reusing the existing delimiter-
   autodetecting `_rows()` helper and `money.parse_signed` (so the unconfirmed delimiter/date/decimal
   convention is handled the same lenient way as every other format here, not assumed).
4. All three fail loudly in Dutch (XML: "geen 'Ntry' ... gevonden"; MT940: "geen ':61:'-transactieregels
   gevonden"; volksbank: existing `_require_column`/`_require_nonempty` machinery) instead of silently
   importing zero transactions, consistent with the other four formats.
5. Fixtures: `tests/test_shadow.py::TestImporters` — `test_camt053_rows`, `test_camt053_bad_xml_and_no_entries`,
   `test_mt940_rows_with_abn_style_structured_narrative`, `test_mt940_no_transaction_lines_fails_loudly`,
   `test_volksbank_rows`, `test_volksbank_missing_amount_column_fails_loudly_in_dutch`. All synthetic (fake
   names, IBANs built with `iban.make_nl`/the existing synthetic set in `samples.py`).

| Format | Status | What's confirmed | Source(s) | Checked |
|---|---|---|---|---|
| Rabobank CSV | **Confirmed** | All 26 column names and order, comma delimiter, every field quoted, date `EEJJ-MM-DD`, amount `+99,01`/`-10,00` | Rabobank's own "Formaatbeschrijving CSV" v1.2 (April 2022, PDF) | pre-existing, re-checked 2026-10-24 |
| Rabobank CSV (cross-check) | **Confirmed** | Column order (IBAN, currency, BIC, internal ref, date, interest date, amount, opposing IBAN/name, SEPA fields, 3x Omschrijving, foreign amount/currency) matches the official spec field-for-field | `firefly-iii/import-configurations`, `nl/rabobank/rabobank-new-csv-format.json` (actively maintained; real users import Rabobank NL files with it) | 2026-10-24 |
| ING CSV (comma, older) | **Confirmed** | Header `"Datum","Naam / Omschrijving","Rekening","Tegenrekening","Code","Af Bij","Bedrag (EUR)","Mutatiesoort","Mededelingen"` | Yuki accounting "ING bank export files" help article (support.yuki.nl) | 2026-10-24 |
| ING CSV (semicolon, newer) | **Confirmed** | Same 9 columns plus `"Saldo na mutatie","Tag"`; semicolon delimiter | `firefly-iii/import-configurations`, `nl/ing/default.json` roles — 11-column order (date, opposing name, account IBAN, opposing IBAN, ignore=Code, debit/credit=Af Bij, amount, tags=Mutatiesoort, note=Mededelingen, ignore=Saldo na mutatie, tags=Tag) lines up index-for-index with the header above | 2026-10-24 |
| ING: date `YYYYMMDD`, decimal comma, `Af`/`Bij` meaning | Not independently re-confirmed this check | — | (unchanged from before this check; common knowledge, not from an ING document) | — |
| ABN AMRO TXT (8-col, no header) | **Confirmed** | Tab-separated, no header, order: account, currency, transaction date, value date, start balance, end balance, amount, description. Columns 2/3/7/8 (0-indexed 1/2/6/7) are the ones this importer reads. | `denilsonsa/abn-amro-statement-parser` README (GitHub; "same columns as XLS, slightly different order") | 2026-10-24 |
| ABN AMRO TXT (10-col, no header, structured opposing party) | **Confirmed — new variant found and now handled** | Tab-separated, no header, order: account, currency, transaction date, 2 ignored columns, interest date, amount, description, **opposing account name, opposing IBAN as their own columns** (not embedded as labelled text in the description). Same index positions for currency/date/amount/description as the 8-col variant. | `firefly-iii/import-configurations`, `nl/abnamro/default.json` (`"headers": false`, tab delimiter, roles: account/currency/date/ignore/ignore/interest-date/amount/description/opposing-name/opposing-iban) | 2026-10-24 |
| ABN AMRO: `/TRTP/.../IBAN/.../NAME/.../REMI/` and `IBAN: … Naam: … Omschrijving:` description styles | Still unconfirmed (from memory) | — | no public source found this check with the exact delimiter/label text; kept as best-effort, the importer degrades gracefully (empty name/IBAN) rather than crashing if neither style matches | — |
| PayPal activity CSV — English core columns | **Confirmed** (pre-existing) | Date, Time, Time Zone, Name, Type, Status, Currency, Gross, Fee, Net, Balance, Transaction ID, Reference Txn ID | third-party guides (unchanged from before this check) | — |
| PayPal activity CSV — Dutch headers | **Confirmed — fixed a wrong guess** | The exact 19 Dutch field names PayPal's own "Activiteitendownload" export uses: `Datum, Tijd, Tijdzone, Naam, Type, Status, Valuta, Bruto, Kosten, Net, Van e-mailadres, Naar e-mailadres, Transactiereferentie, BTW, Reference TxnID, Factuurnummer, Ontvangstbewijsreferentie, Saldo, Onderwerp`. "Type" and "Status" are the **same word** in Dutch and English — the old alias guess `"beschrijving"` for the "type" column was wrong (not a real PayPal header) and has been removed. | SnelStart "PayPal afschriften inlezen" kennisplein article (kennispleinweb.snelstart.nl/nl/paypal) — SnelStart is mainstream Dutch accounting software; the article states all 19 fields are mandatory for its own PayPal import | 2026-10-24 |
| PayPal "Note"/"Notitie" and "Item Title"/"Objecttitel" | **Still unconfirmed** | — | Not present in the SnelStart 19-field list above, so no independent Dutch source was found. Kept as best-effort guesses — missing them only loses optional description enrichment text, never drops a transaction. | — |

## What changed in the importer (`countersign_shadow/importers/transactions.py`)

1. **PayPal**: removed the wrong `"beschrijving"` guess for the Dutch "type" column (the real Dutch header is
   `Type`, unchanged from English).
2. **ABN AMRO**: added support for the 10-column no-header layout where the opposing account name and IBAN are
   their own columns (confirmed via firefly-iii's config above), instead of only looking for them as labelled
   text inside the description. Also fixed a latent bug where, on any no-header file with more than 8 columns,
   every column past the 7th was being joined into the description field (`"\t".join(f[7:])`) — this silently
   polluted the description with raw opposing-name/IBAN values instead of reading them as structured fields.
3. **All four formats**: the importer now **fails loudly, in Dutch, naming the missing column** when the
   amount column is not found at all (`ImportError_`, e.g. `"ING-bestand: kolom 'Bedrag (EUR)' niet gevonden; ..."`),
   instead of silently importing zero transactions. A second safety net raises the same kind of error if every
   row is read but none produces a parsed transaction (covers cases not caught by the specific column check).
4. Added fixture-based tests (synthetic rows, exact confirmed header text) for: the 10-column ABN AMRO opposing-
   column layout, the real Dutch PayPal header row, and the new "fails loudly in Dutch" behaviour for all four
   formats. Full fixtures: `tests/test_shadow.py::TestImporters`.

## Known limits (also logged in README)

- ABN AMRO's `/TRTP/.../IBAN/.../NAME/.../REMI/` and `IBAN: … Naam: … Omschrijving:` description styles remain
  unconfirmed against a public source; if a real file uses neither style and lacks the structured 10-column
  layout, the row still imports but with an empty counterparty name/IBAN (checked by rule C5 "new IBAN" logic
  as "never paid before", not silently trusted).
- PayPal's Dutch "Note"/"Item Title" columns (`notitie`/`objecttitel`) are unconfirmed guesses; if wrong, the
  importer just loses optional description text, never a transaction.
- ING's date format (`YYYYMMDD`), decimal-comma convention and the meaning of `Af`/`Bij` are common knowledge,
  not confirmed against an ING-published document.
- No real bank or PayPal export file was available for this pilot kit. **The first pilot user's real file is
  the real test.** A file that doesn't match any of the above now fails with a clear message (English for
  "unrecognised format entirely", Dutch for "this column is missing") instead of being silently misread.
