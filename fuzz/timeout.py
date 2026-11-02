# SPDX-License-Identifier: Apache-2.0
"""Hard per-case timeout for one importer call: SIGALRM-based.

SIGALRM only works on Linux/macOS and only from a process's main thread -- both true for a
multiprocessing.Pool worker, which is what every caller here is. This is a real interrupt (not just a
"measure after the fact" deadline), so it also catches genuine hangs, not only slow-but-finite calls.
"""
from __future__ import annotations

import signal
from contextlib import contextmanager


class HangTimeout(Exception):
    """Raised when one case's importer call did not return within the per-case time budget."""


@contextmanager
def timeout_guard(seconds: float = 5.0):
    def _on_alarm(signum, frame):
        raise HangTimeout(f"did not return within {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, _on_alarm)
    old_itimer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, old_itimer[0] if old_itimer[0] else 0.0)
        signal.signal(signal.SIGALRM, old_handler)
