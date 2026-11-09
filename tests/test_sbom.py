# SPDX-License-Identifier: Apache-2.0
"""The shipped SBOM (sbom/countersign.cdx.json, written by tools/sbom.sh) must describe what is vendored."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "countersign_shadow" / "_vendor"


def vendored_versions():
    pypdf = re.search(r'^__version__ = "([^"]+)"', (VENDOR / "pypdf" / "_version.py").read_text(), re.M)
    ole = re.search(r"^__version__ = '([^']+)'", (VENDOR / "olefile" / "olefile.py").read_text(), re.M)
    return {"pypdf": pypdf.group(1).strip(), "olefile": ole.group(1).strip()}


class ShippedSbom(unittest.TestCase):
    def setUp(self):
        self.bom = json.loads((ROOT / "sbom" / "countersign.cdx.json").read_text())

    def test_cyclonedx_header(self):
        self.assertEqual(self.bom["bomFormat"], "CycloneDX")
        self.assertEqual(self.bom["specVersion"], "1.6")
        root = self.bom["metadata"]["component"]
        self.assertEqual(root["name"], "countersign-shadow")
        self.assertEqual(root["licenses"][0]["license"]["id"], "Apache-2.0")

    def test_components_match_vendored_code(self):
        got = {c["name"]: c["version"] for c in self.bom["components"]}
        self.assertEqual(got, vendored_versions(), "vendored code changed: run tools/sbom.sh")
        for c in self.bom["components"]:
            self.assertEqual(c["purl"], f"pkg:pypi/{c['name']}@{c['version']}")
            self.assertTrue((ROOT / next(p["value"] for p in c["properties"]
                                         if p["name"] == "countersign:vendored-path")).is_dir())

    def test_no_local_paths(self):
        def strings(o):
            if isinstance(o, dict):
                for v in o.values():
                    yield from strings(v)
            elif isinstance(o, list):
                for v in o:
                    yield from strings(v)
            elif isinstance(o, str):
                yield o
        local = [s for s in strings(self.bom) if s.startswith(("/", "~")) or s[1:3] == ":\\"]
        self.assertEqual(local, [])


if __name__ == "__main__":
    unittest.main()
