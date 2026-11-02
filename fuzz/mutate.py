# SPDX-License-Identifier: Apache-2.0
"""Mutation fuzzing: corrupt bytes (real samples/ files, or our own freshly generated valid files).

No oracle here (mutated bytes have no meaningful ground truth) -- these cases only check property (a)
"never an unhandled exception, only the importer's documented error type" and (b) "no hang".
"""
from __future__ import annotations

from pathlib import Path

from hypothesis import strategies as st

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

# format -> real sample file(s) under samples/ to mutate, if any (camt053/mt940/volksbank have none yet,
# so those fall back to a freshly generated valid file as the mutation seed -- see runner.py).
SEED_FILES = {
    "paypal": ["paypal.csv"], "ing": ["ing.csv"], "rabobank": ["rabobank.csv"], "abnamro": ["abnamro.txt"],
}


def real_seed_bytes(fmt: str) -> bytes | None:
    for name in SEED_FILES.get(fmt, []):
        p = SAMPLES_DIR / name
        if p.exists():
            return p.read_bytes()
    return None


@st.composite
def st_mutation(draw, data: bytes) -> bytes:
    if not data:
        return data
    op = draw(st.sampled_from(["flip", "truncate", "dup_chunk", "delete_chunk", "huge_value", "insert_junk",
                                "bit_in_header", "zero_fill"]))
    b = bytearray(data)
    n = len(b)
    if op == "flip":
        k = draw(st.integers(1, min(50, n)))
        for _ in range(k):
            i = draw(st.integers(0, n - 1))
            b[i] ^= draw(st.integers(1, 255))
    elif op == "truncate":
        cut = draw(st.integers(0, n))
        b = b[:cut]
    elif op == "dup_chunk":
        a, c = sorted(draw(st.tuples(st.integers(0, n), st.integers(0, n))))
        chunk = bytes(b[a:c])
        pos = draw(st.integers(0, n))
        b = b[:pos] + chunk + chunk + b[pos:]
    elif op == "delete_chunk":
        a, c = sorted(draw(st.tuples(st.integers(0, n), st.integers(0, n))))
        b = b[:a] + b[c:]
    elif op == "huge_value":
        pos = draw(st.integers(0, n))
        junk = draw(st.sampled_from([b"9" * 200, b"-" * 200, b"0" * 500, b"," * 100, b"\xff" * 64]))
        b = b[:pos] + junk + b[pos:]
    elif op == "insert_junk":
        pos = draw(st.integers(0, n))
        junk = draw(st.binary(min_size=1, max_size=200))
        b = b[:pos] + junk + b[pos:]
    elif op == "bit_in_header":
        k = draw(st.integers(0, min(64, n) - 1)) if n else 0
        b[k] ^= 0x80
    elif op == "zero_fill":
        a, c = sorted(draw(st.tuples(st.integers(0, n), st.integers(0, n))))
        b[a:c] = b"\0" * (c - a)
    return bytes(b)
