#!/usr/bin/env python3
"""Generate tiny, safe Feishu bridge acceptance-test files.

Creates a UTF-8 text file, a PNG (Pillow when available, otherwise a
stdlib PNG), and—when ffmpeg exists—a 3-second MP4. Writes only under
<bridge>/test_files/. No credentials are read.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "test_files"


def make_png(path: Path) -> None:
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (900, 420), (38, 92, 160))
        d = ImageDraw.Draw(img)
        d.rectangle([30, 30, 870, 390], outline=(255, 255, 255), width=4)
        d.text((70, 150), "Feishu image attachment test", fill=(255, 255, 255))
        d.text((70, 210), "Agent bridge attachment check", fill=(220, 235, 255))
        img.save(path)
        return
    except Exception:
        pass
    width, height = 640, 360
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")
        for x in range(width):
            r, g, b = 40, 90 + (y * 80 // height), 170
            if 40 < x < 600 and 40 < y < 320 and (x // 40 + y // 40) % 2 == 0:
                r, g, b = 235, 242, 255
            row += bytes((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        out = struct.pack(">I", len(data)) + kind + data
        return out + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    txt = OUT / "feishu_attachment_test.txt"
    txt.write_text(
        "飞书附件发送测试\n\n"
        "如果你在飞书里能打开并看到这段文字，说明文件附件通道正常。\n",
        encoding="utf-8",
    )
    print(f"created {txt}")
    png = OUT / "feishu_attachment_test.png"
    make_png(png)
    print(f"created {png}")
    ffmpeg = shutil.which("ffmpeg")
    mp4 = OUT / "feishu_attachment_test.mp4"
    if ffmpeg:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=3:size=640x360:rate=30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(mp4),
            ],
            check=True,
        )
        print(f"created {mp4}")
    else:
        print("ffmpeg not found; skipped mp4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
