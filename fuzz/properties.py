# SPDX-License-Identifier: Apache-2.0
"""Hypothesis property tests. Called directly (no pytest) by runner.py, in a loop, per worker process.

Each property function, on ANY exception (including a failed oracle assertion), stashes the exact bytes
that produced it into LAST_FAILURE[key] before re-raising -- so after Hypothesis finishes shrinking and
the final (minimal) exception propagates out of the `@given`-decorated call, the caller can retrieve the
minimal reproducer bytes alongside the traceback.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

# pypdf logs some malformed-PDF conditions at ERROR, from various pypdf.* submodule loggers (not just
# DEBUG/WARNING, which attachments.py already silences on the "pypdf" logger specifically) -- expected
# noise on deliberately-mutated PDFs, but can be enormous (a full encoding table dumped per message) and
# would otherwise flood fuzz/logs/ over a 6h run. Global, since this is a throwaway fuzz worker process;
# attachments.py's own crash handling (what we're actually testing) is unaffected either way.
logging.disable(logging.CRITICAL)

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from . import formats, genlib as g, mutate, oracle
from .timeout import timeout_guard

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from countersign_shadow import checks, iban as ibanlib
from countersign_shadow.importers import attachments, msg, transactions
from countersign_shadow._vendor import load as _vendor_load

# import the vendored pypdf/olefile ONCE here, at worker start, outside every timed case. The
# importers load them lazily on first use; in the first overnight run that first use fell inside the 5s
# SIGALRM budget while Hypothesis's line tracer (scrutineer) was active, so importing pypdf's huge glyph /
# font-metric tables took > 5s, the alarm interrupted the import half-way, and every later case re-imported
# and timed out again -- 95 "HangTimeout" findings (incl. all 116 failing valid:pdf passes) that were never
# a hang in pdf_text itself.
_vendor_load("pypdf")
_vendor_load("olefile")

settings.register_profile(
    "fuzz", max_examples=40, deadline=None, database=None, print_blob=False,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much])
settings.load_profile("fuzz")

PER_CASE_TIMEOUT = 5.0
LAST_FAILURE: dict[str, tuple] = {}

# The only exception types a malformed (but structurally plausible) import is allowed to raise.
DOCUMENTED_ERRORS = {
    "transactions": (transactions.ImportError_,),
    "msg": (msg.MsgUnsupported,),
    "pdf": (),  # pdf_text()'s whole contract is "never raises"
}


def _stash(key: str, content: bytes, note: str, exc: BaseException) -> None:
    LAST_FAILURE[key] = (content, note, exc)


def _tmpfile(data: bytes, suffix: str = ".txt") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="fuzz-")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


# --------------------------------------------------------------------------------------------- transactions.*
def make_prop_valid_roundtrip(fmt: str):
    @settings.get_profile("fuzz")
    @given(data=st.data())
    def prop(data):
        content, truth = formats.BUILDERS[fmt](data.draw)
        path = _tmpfile(content, ".xml" if fmt == "camt053" else ".txt")
        try:
            warnings: list[str] = []
            with timeout_guard(PER_CASE_TIMEOUT):
                kind, parsed = transactions.import_file(path, fmt=fmt, warnings=warnings)
            problems = oracle.compare(fmt, parsed, truth)
            if warnings:
                problems.append(f"valid file produced skip warnings: {warnings[:2]}")
            if problems:
                raise AssertionError(f"{fmt} round-trip mismatch: {problems[:3]}")
        except Exception as exc:
            _stash(f"valid:{fmt}", content, f"{len(truth)} generated rows", exc)
            raise
        finally:
            os.unlink(path)
    prop.__name__ = f"prop_valid_roundtrip_{fmt}"
    return prop


def make_prop_mutation(fmt: str):
    seed = mutate.real_seed_bytes(fmt)

    @settings.get_profile("fuzz")
    @given(data=st.data())
    def prop(data):
        base = seed if seed is not None else formats.BUILDERS[fmt](data.draw)[0]
        content = data.draw(mutate.st_mutation(base))
        path = _tmpfile(content, ".xml" if fmt == "camt053" else ".txt")
        try:
            with timeout_guard(PER_CASE_TIMEOUT):
                try:
                    transactions.import_file(path, fmt=fmt, warnings=[])
                except transactions.ImportCrash as crash:
                    # the user sees a friendly message, but an unexpected internal error is still a bug:
                    # report the original exception (with its own traceback) as the finding
                    raise crash.__cause__ or crash
                except DOCUMENTED_ERRORS["transactions"]:
                    pass  # a loud, documented, Dutch-language error is the *correct* outcome here
        except Exception as exc:
            _stash(f"mutated:{fmt}", content, "mutated sample/generated file", exc)
            raise
        finally:
            os.unlink(path)
    prop.__name__ = f"prop_mutation_{fmt}"
    return prop


# --------------------------------------------------------------------------------------------- PDF
@settings.get_profile("fuzz")
@given(data=st.data())
def prop_pdf_valid_roundtrip(data):
    content, truth = formats.build_pdf(data.draw)
    try:
        with timeout_guard(PER_CASE_TIMEOUT):
            text, warning = attachments.pdf_text(content)
            info = attachments.parse_invoice(text) if text else None
        if warning or info is None:
            raise AssertionError(f"valid PDF came back unreadable: {warning!r}")
        if truth["iban"] not in info["ibans"]:
            raise AssertionError(f"IBAN {truth['iban']} not recovered from PDF text: {info['ibans']}")
        if truth["amount"] not in info["amounts"] and truth["amount"] != info.get("total"):
            raise AssertionError(f"amount {truth['amount']} not recovered from PDF text: {info['amounts']}")
    except Exception as exc:
        _stash("valid:pdf", content, str(truth), exc)
        raise


@settings.get_profile("fuzz")
@given(data=st.data())
def prop_pdf_mutation(data):
    base = formats.build_pdf(data.draw)[0]
    content = data.draw(mutate.st_mutation(base))
    try:
        with timeout_guard(PER_CASE_TIMEOUT):
            attachments.pdf_text(content)  # contract: text or a Dutch warning, never an exception
    except Exception as exc:
        _stash("mutated:pdf", content, "mutated generated PDF", exc)
        raise


# --------------------------------------------------------------------------------------------- .msg
@settings.get_profile("fuzz")
@given(data=st.data())
def prop_msg_valid_roundtrip(data):
    content, truth = formats.build_msg(data.draw)
    path = _tmpfile(content, ".msg")
    try:
        with timeout_guard(PER_CASE_TIMEOUT):
            parts = msg.read_msg_parts(Path(path))
        if parts.get("subject") != truth["subject"]:
            raise AssertionError(f"subject {parts.get('subject')!r} != expected {truth['subject']!r}")
        if parts.get("from") != truth["from"].lower():
            raise AssertionError(f"from {parts.get('from')!r} != expected {truth['from'].lower()!r}")
    except Exception as exc:
        _stash("valid:msg", content, str(truth), exc)
        raise
    finally:
        os.unlink(path)


@settings.get_profile("fuzz")
@given(data=st.data())
def prop_msg_mutation(data):
    base = formats.build_msg(data.draw)[0]
    content = data.draw(mutate.st_mutation(base))
    path = _tmpfile(content, ".msg")
    try:
        with timeout_guard(PER_CASE_TIMEOUT):
            try:
                msg.read_msg_parts(Path(path))
            except DOCUMENTED_ERRORS["msg"]:
                pass
    except Exception as exc:
        _stash("mutated:msg", content, "mutated generated .msg", exc)
        raise
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------------------------- checks.py: property (d)
@settings.get_profile("fuzz")
@given(data=st.data())
def prop_new_iban_never_silently_safe(data):
    """A pay_invoice proposal to a brand-new IBAN (never seen in history, not in policy.known_ibans),
    with every other check satisfied, must never come back verdict 'ok' or 'none' -- it must be
    blocked or at least warned, so the pilot user is never told a brand-new-IBAN payment is safe."""
    new_iban = data.draw(g.st_iban())
    amount = data.draw(g.st_amount_cents(1_00, 5000_00))
    amount_str = g.cents_to_str(amount)
    supplier = "Kartonfabriek Noord BV"
    quote = f"Betaal {amount_str} naar {new_iban} t.n.v. {supplier}."
    message = {"subject": "Factuur", "body": quote, "from": "info@kartonfabriek-noord.example",
               "from_name": supplier, "date": "2026-10-24", "pdfs": []}
    proposal = {"action": "pay_invoice", "amount": amount_str, "currency": "EUR",
                "payee": {"name": supplier, "iban": new_iban, "email": ""},
                "invoice_ref": "", "order_ref": "", "evidence_quote": quote, "rationale": ""}
    # history has OTHER iban-having transactions, but never this one -- not known, not even by supplier name
    history_iban = ibanlib.make_nl("RABO", "0000000001")
    # the generator can (and after shrinking, does) draw exactly this IBAN; then it is NOT new and
    # verdict 'ok' is correct -- that was the property's own bug, not checks.py's.
    assume(ibanlib.normalise(new_iban) != history_iban)
    history = [{"amount": "-10.00", "currency": "EUR", "date": "2026-01-01", "iban": history_iban,
                "counterparty": "Someone Else BV", "email": "", "description": "", "reference": "", "status": ""}]
    policy = checks.Policy()
    try:
        with timeout_guard(PER_CASE_TIMEOUT):
            result = checks.evaluate(proposal, message, history, policy)
            v = checks.verdict("pay_invoice", result)
        if v in ("ok", "none"):
            raise AssertionError(f"new-IBAN {new_iban} payment of {amount_str} came back verdict={v!r}")
    except Exception as exc:
        _stash("checks:new_iban", quote.encode(), f"iban={new_iban} amount={amount_str}", exc)
        raise
