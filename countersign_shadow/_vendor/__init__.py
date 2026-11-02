# SPDX-License-Identifier: Apache-2.0
"""Vendored, unmodified copies of two pure-Python BSD libraries, so pilot users need no `pip install`:

    pypdf 6.21.2    BSD-3-Clause  (pypdf/LICENSE)    text layer of PDF attachments
    olefile 0.47    BSD-2-Clause  (olefile/LICENSE)  Outlook .msg (OLE compound files)

`load(name)` imports the vendored copy first and falls back to an installed one. pypdf's optional extras
(cryptography / pycryptodome for AES-encrypted PDFs, Pillow for images) are not vendored and not needed for text.
To update: copy the package folder from a fresh `pip install` here, keep its LICENSE, bump the versions above.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

VENDOR = str(Path(__file__).resolve().parent)
VERSIONS = {"pypdf": "6.21.2", "olefile": "0.47"}


def load(name: str):
    """Import `name` (pypdf | olefile): vendored copy first, else installed. Raises ImportError if neither works."""
    if name in sys.modules and sys.modules[name] is None:  # explicitly blocked (tests)
        raise ImportError(name)
    if VENDOR not in sys.path:
        sys.path.insert(0, VENDOR)  # the vendored pypdf uses absolute "from pypdf..." imports
    try:
        return importlib.import_module(name)
    except ImportError:
        sys.path.remove(VENDOR)
        for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[mod]
        return importlib.import_module(name)


def load_tests(loader, tests, pattern):
    """`python3 -m unittest discover` from the repo root must not import the vendored
    libraries as test packages (pypdf imports itself by its top-level name). Nothing to test here."""
    return tests
