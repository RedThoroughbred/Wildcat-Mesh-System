"""Generate the PWA icon PNGs with nothing but the stdlib (no Pillow on a Pi).

    python -m wildcat.observatory.icons observatory/static/v2/icons

Draws the Wildcat mark: a terracotta rounded square with a warm radial glow and
a cream "signal" dot + two arcs. Sizes: 192, 512 (any + maskable) and 180
(apple-touch-icon). Deterministic — commit the PNGs.
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path


def _png(width: int, height: int, rows: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)          # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def render(size: int, maskable: bool = False) -> bytes:
    s = size
    pad = 0.0 if maskable else 0.06 * s              # maskable icons fill the whole square
    radius = 0.22 * s
    cx, cy = s / 2, s / 2
    rows = bytearray()
    for y in range(s):
        rows.append(0)                               # filter: none
        for x in range(s):
            # rounded-square mask
            dx = max(pad + radius - x, x - (s - pad - radius), 0)
            dy = max(pad + radius - y, y - (s - pad - radius), 0)
            inside = (dx * dx + dy * dy) <= radius * radius and pad <= x < s - pad and pad <= y < s - pad
            if not inside:
                rows += b"\x00\x00\x00\x00"
                continue
            # terracotta with a warm glow top-left
            gx, gy = (x - 0.35 * s) / s, (y - 0.32 * s) / s
            g = max(0.0, 1.0 - math.sqrt(gx * gx + gy * gy) * 1.6)
            r = int(224 + (255 - 224) * g * 0.6); gch = int(112 + (176 - 112) * g * 0.6); b = int(75 + (138 - 75) * g * 0.6)
            # the mark: a cream dot + two arcs (a node radiating)
            d = math.hypot(x - cx * 0.86, y - cy * 1.12)
            ring = None
            for rr, w in ((0.30 * s, 0.045 * s), (0.44 * s, 0.045 * s)):
                if abs(d - rr) <= w / 2 and (x - cx * 0.86) > -0.02 * s and (y - cy * 1.12) < 0.02 * s:
                    ring = True
            if d <= 0.11 * s or ring:
                r, gch, b = 250, 246, 240
            rows += bytes((r, gch, b, 255))
    return _png(s, s, bytes(rows))


def main(out_dir: str) -> None:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / "icon-192.png").write_bytes(render(192))
    (out / "icon-512.png").write_bytes(render(512))
    (out / "icon-512-maskable.png").write_bytes(render(512, maskable=True))
    (out / "apple-touch-icon.png").write_bytes(render(180, maskable=True))   # iOS rounds the corners itself
    print("wrote icons to", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "observatory/static/v2/icons")
