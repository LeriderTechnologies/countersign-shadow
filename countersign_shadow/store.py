# SPDX-License-Identifier: Apache-2.0
"""Local workspace: plain JSON files in one folder on the pilot user's own machine.

    messages.json      imported e-mails
    transactions.json  imported PayPal / bank rows
    cards.json         drafted decision cards (shadow mode: nothing is ever executed)
    feedback.json      the pilot user's marks: right / wrong / dangerous + comment
    policy.json        optional overrides of the default limits (see checks.Policy)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def load(self, name: str, default):
        p = self._path(name)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, name: str, data) -> None:
        p = self._path(name)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)

    def merge(self, name: str, items: list[dict]) -> tuple[int, int]:
        """Add items by id; returns (added, total)."""
        cur = self.load(name, [])
        ids = {x["id"] for x in cur}
        new = [x for x in items if x["id"] not in ids]
        self.save(name, cur + new)
        return len(new), len(cur) + len(new)

    @property
    def messages(self) -> list[dict]:
        return self.load("messages", [])

    @property
    def transactions(self) -> list[dict]:
        return self.load("transactions", [])
