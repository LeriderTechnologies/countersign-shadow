# SPDX-License-Identifier: Apache-2.0
"""One task = one call into the Hypothesis engine for one (format, property) pair.

Calling a `@given`-decorated function directly (no pytest) runs Hypothesis's normal engine: it draws up
to `max_examples` examples, and on the first failing one, shrinks to a minimal failing example and
re-raises it. properties.py stashes that minimal example's bytes right before each re-raise, so after
the call raises here we can recover (bytes, note, exc) for the *shrunk* reproducer, not some arbitrary
earlier draw.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fuzz import properties
from fuzz.timeout import HangTimeout

TRANSACTION_FORMATS = ("paypal", "ing", "rabobank", "abnamro", "camt053", "mt940", "volksbank")

TASKS: dict[str, callable] = {}
for _fmt in TRANSACTION_FORMATS:
    TASKS[f"valid:{_fmt}"] = properties.make_prop_valid_roundtrip(_fmt)
    TASKS[f"mutated:{_fmt}"] = properties.make_prop_mutation(_fmt)
TASKS["valid:pdf"] = properties.prop_pdf_valid_roundtrip
TASKS["mutated:pdf"] = properties.prop_pdf_mutation
TASKS["valid:msg"] = properties.prop_msg_valid_roundtrip
TASKS["mutated:msg"] = properties.prop_msg_mutation
TASKS["checks:new_iban"] = properties.prop_new_iban_never_silently_safe

TASK_NAMES = list(TASKS)


def _top_frame(tb) -> str:
    frames = traceback.extract_tb(tb)
    # the deepest frame inside countersign_shadow/ (skip our own harness/hypothesis internals when possible)
    for fr in reversed(frames):
        if "countersign_shadow" in fr.filename:
            return f"{Path(fr.filename).name}:{fr.lineno}:{fr.name}"
    return f"{Path(frames[-1].filename).name}:{frames[-1].lineno}:{frames[-1].name}" if frames else "?"


def run_one(task_name: str) -> dict:
    """Run one Hypothesis engine pass (up to max_examples draws) for one task. Returns a result dict;
    never raises (any exception from the task itself is captured, not propagated)."""
    fn = TASKS[task_name]
    t0 = time.monotonic()
    properties.LAST_FAILURE.pop(task_name, None)
    try:
        fn()
        return {"task": task_name, "ok": True, "seconds": time.monotonic() - t0}
    except BaseException as exc:  # noqa: BLE001 -- we want literally everything, that's the point
        content, note, inner = properties.LAST_FAILURE.get(task_name, (b"", "", exc))
        # `inner` is stashed right at the `raise` inside the property function, so inner.__traceback__
        # still has the full original call chain into countersign_shadow/. The exception Hypothesis
        # finally re-raises out of fn() (`exc`) is the same exception after shrinking, but by the time
        # it gets here its traceback can be dominated by Hypothesis's own engine frames -- prefer inner's.
        tb = inner.__traceback__ or exc.__traceback__
        is_hang = isinstance(inner, HangTimeout) or isinstance(exc, HangTimeout)
        return {
            "task": task_name, "ok": False, "seconds": time.monotonic() - t0,
            "exc_type": type(inner).__name__, "exc_msg": str(inner)[:2000],
            "top_frame": _top_frame(tb) if tb else "?",
            "traceback": "".join(traceback.format_exception(type(inner), inner, tb))[:20000],
            "content": content, "note": note, "is_hang": is_hang,
        }
