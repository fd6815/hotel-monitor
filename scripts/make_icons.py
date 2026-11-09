"""產生手機「加到主畫面」用的圖示 site/icon-*.png（純 Python，不需要 Pillow）。
圖案：主色圓角方塊＋白色飯店大樓（窗戶挖空）＋右下角金色圓點（價格標記）。"""
import struct
import zlib
from pathlib import Path

BG = (15, 110, 110)
FG = (255, 255, 255)
GOLD = (233, 178, 64)


def inside_round_rect(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def pixel(u, v, maskable):
    """u、v 為 0–1 座標；回傳 RGBA。"""
    if not maskable and not inside_round_rect(u, v, 0, 0, 1, 1, 0.22):
        return (0, 0, 0, 0)
    s = 0.8 if maskable else 1.0          # maskable 圖示四周留安全區
    x = (u - 0.5) / s + 0.5
    y = (v - 0.5) / s + 0.5
    # 金色圓點
    if (x - 0.74) ** 2 + (y - 0.72) ** 2 <= 0.13 ** 2:
        return GOLD + (255,)
    # 大樓
    if 0.24 <= x <= 0.6 and 0.2 <= y <= 0.82:
        col = int((x - 0.29) / 0.09)
        row = int((y - 0.27) / 0.1)
        wx = (x - 0.29) % 0.09
        wy = (y - 0.27) % 0.1
        is_window = 0 <= col < 3 and 0 <= row < 4 and wx < 0.05 and wy < 0.055 and x >= 0.29 and y >= 0.27
        door = 0.38 <= x <= 0.46 and y >= 0.7
        return (BG if is_window or door else FG) + (255,)
    return BG + (255,)


def png(size, maskable=False):
    rows = []
    ss = 3   # 超取樣抗鋸齒
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            acc = [0, 0, 0, 0]
            for a in range(ss):
                for b in range(ss):
                    p = pixel((i + (a + 0.5) / ss) / size, (j + (b + 0.5) / ss) / size, maskable)
                    for k in range(3):
                        acc[k] += p[k] * p[3]
                    acc[3] += p[3]
            n = ss * ss
            al = acc[3] / n
            row += bytes([int(acc[k] / acc[3]) if acc[3] else 0 for k in range(3)] + [int(al)])
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "site"
    (out / "icon-192.png").write_bytes(png(192))
    (out / "icon-512.png").write_bytes(png(512))
    (out / "icon-maskable-512.png").write_bytes(png(512, maskable=True))
    (out / "apple-touch-icon.png").write_bytes(png(180, maskable=True))   # iOS 自己會切圓角
    print("ok")
