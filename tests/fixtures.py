# SPDX-License-Identifier: Apache-2.0
"""Synthetic test files built by hand, no extra libraries: a minimal text PDF and a minimal Outlook .msg.

make_pdf(lines)            one-page PDF, Helvetica + WinAnsiEncoding (so "€" works), text layer only
make_image_only_pdf()      one-page PDF with a drawn rectangle and no text (stands in for a scanned invoice)
make_msg(...)              OLE compound file (CFB v3) with the MAPI property streams Outlook writes
"""
from __future__ import annotations

import struct
from datetime import datetime, timezone


# ---------------------------------------------------------------- PDF
def _pdf(content: bytes, with_font: bool = True) -> bytes:
    font = b"/Font << /F1 5 0 R >>" if with_font else b""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << " + font +
        b" >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def make_pdf(lines: list[str]) -> bytes:
    ops = [b"BT /F1 11 Tf 14 TL 50 790 Td"]
    for ln in lines:
        esc = ln.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        ops.append(b"(" + esc + b") Tj T*")
    ops.append(b"ET")
    return _pdf(b"\n".join(ops))


def make_image_only_pdf() -> bytes:
    return _pdf(b"0.5 g 50 500 400 300 re f", with_font=False)


# ---------------------------------------------------------------- Outlook .msg (CFB v3)
FREE, END, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


def _cfb(tree: dict) -> bytes:
    """tree: {name: bytes (stream) | dict (storage)} -> compound file bytes. Streams < 4096 bytes go into the mini
    stream, as the format requires. Siblings are chained through the right-sibling pointer (a valid, if
    unbalanced, tree; readers only follow the pointers)."""
    entries = [{"name": "Root Entry", "type": 5, "child": NOSTREAM, "right": NOSTREAM, "data": b""}]

    def add(node: dict) -> int:
        first = prev = None
        for name, val in node.items():
            idx = len(entries)
            e = {"name": name, "type": 1 if isinstance(val, dict) else 2, "child": NOSTREAM, "right": NOSTREAM,
                 "data": b"" if isinstance(val, dict) else val}
            entries.append(e)
            if isinstance(val, dict):
                e["child"] = add(val)
            if prev is None:
                first = idx
            else:
                entries[prev]["right"] = idx
            prev = idx
        return NOSTREAM if first is None else first

    entries[0]["child"] = add(tree)
    mini, minifat, big = bytearray(), [], []
    for e in entries[1:]:
        if e["type"] != 2:
            e["start"], e["size"] = 0, 0
            continue
        d = e["data"]
        e["size"] = len(d)
        if len(d) < 4096:
            e["start"] = len(mini) // 64 if d else END
            n = -(-len(d) // 64)
            minifat += [len(minifat) + k + 1 for k in range(n)]
            if n:
                minifat[-1] = END
            mini += d + b"\0" * (n * 64 - len(d))
        else:
            big.append(e)

    def sectors(n_bytes: int) -> int:
        return -(-n_bytes // 512)

    n_dir = sectors(len(entries) * 128)
    n_minifat = sectors(len(minifat) * 4)
    n_mini = sectors(len(mini))
    n_big = [sectors(len(e["data"])) for e in big]
    n_data = n_dir + n_minifat + n_mini + sum(n_big)
    n_fat = 1
    while n_fat * 128 < n_fat + n_data:
        n_fat += 1
    fat: list[int] = [FATSECT] * n_fat
    def chain(n: int) -> int:
        start = len(fat)
        fat.extend(start + k + 1 for k in range(n))
        fat[-1] = END
        return start
    dir_start = chain(n_dir)
    minifat_start = chain(n_minifat) if n_minifat else END
    mini_start = chain(n_mini) if n_mini else END
    for e, n in zip(big, n_big):
        e["start"] = chain(n)
    fat += [FREE] * (n_fat * 128 - len(fat))
    entries[0]["start"], entries[0]["size"] = mini_start, len(mini)

    def dirent(e: dict | None) -> bytes:
        if e is None:
            return b"\0" * 64 + struct.pack("<HBBIII", 0, 0, 0, NOSTREAM, NOSTREAM, NOSTREAM) + b"\0" * 36 + \
                struct.pack("<IQ", 0, 0)
        name = e["name"].encode("utf-16-le") + b"\0\0"
        return (name.ljust(64, b"\0") + struct.pack("<HBBIII", len(name), e["type"], 1, NOSTREAM, e["right"],
                                                     e["child"]) + b"\0" * 36 + struct.pack("<IQ", e["start"], e["size"]))
    dirdata = b"".join(dirent(e) for e in entries)
    dirdata += b"".join(dirent(None) for _ in range(n_dir * 4 - len(entries)))
    difat = list(range(n_fat)) + [FREE] * (109 - n_fat)
    header = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) +
              b"\0" * 6 + struct.pack("<IIIIIIIII", 0, n_fat, dir_start, 0, 4096, minifat_start, n_minifat, END, 0) +
              struct.pack("<109I", *difat))
    body = struct.pack(f"<{len(fat)}I", *fat) + dirdata
    body += struct.pack(f"<{len(minifat)}I", *minifat).ljust(n_minifat * 512, b"\xff")
    body += bytes(mini).ljust(n_mini * 512, b"\0")
    for e, n in zip(big, n_big):
        body += e["data"].ljust(n * 512, b"\0")
    return header + body


def _u(s: str) -> bytes:
    return s.encode("utf-16-le")


def make_msg(subject: str, sender_name: str, sender: str, body: str, sent: datetime,
             attachments: list[tuple[str, str, bytes]] = ()) -> bytes:
    ft = int((sent - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000)
    props = b"\0" * 32 + struct.pack("<IIQ", (0x0039 << 16) | 0x0040, 6, ft)
    tree: dict = {
        "__substg1.0_0037001F": _u(subject), "__substg1.0_0C1A001F": _u(sender_name),
        "__substg1.0_0C1F001F": _u("/O=EXCHANGELABS/OU=EXCHANGE/CN=RECIPIENTS/CN=X"),  # X.500, not an address
        "__substg1.0_5D01001F": _u(sender), "__substg1.0_0E04001F": _u("Sanne"),
        "__substg1.0_1000001F": _u(body), "__substg1.0_1035001F": _u(f"<{subject[:10]}@test.example>"),
        "__properties_version1.0": props,
    }
    for i, (name, ctype, data) in enumerate(attachments):
        tree[f"__attach_version1.0_#{i:08X}"] = {
            "__substg1.0_3707001F": _u(name), "__substg1.0_370E001F": _u(ctype), "__substg1.0_37010102": data,
            "__properties_version1.0": b"\0" * 8}
    return _cfb(tree)
