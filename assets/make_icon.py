"""Gera assets/gamedock.ico (16 a 256 px) desenhando a marca pixel a pixel, só com stdlib.

Marca: quadrado arredondado escuro, ▶ branco e a "doca" com o gradiente rosa → laranja → ciano
(as cores mudam como o app muda de cor conforme o jogo).
Uso:  python assets/make_icon.py
"""
import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4
BG = (12, 12, 16)
GRADIENT = ((255, 61, 129), (255, 178, 61), (61, 214, 255))


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(t):
    return lerp(GRADIENT[0], GRADIENT[1], t * 2) if t < .5 else lerp(GRADIENT[1], GRADIENT[2], (t - .5) * 2)


def in_round_rect(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r and x0 <= x <= x1 and y0 <= y <= y1


def in_triangle(x, y, a, b, c):
    def side(p, q):
        return (x - q[0]) * (p[1] - q[1]) - (p[0] - q[0]) * (y - q[1])
    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def color_at(u, v):
    """u, v em 0..1. Retorna (r, g, b, a)."""
    if not in_round_rect(u, v, 0, 0, 1, 1, .22):
        return (0, 0, 0, 0)
    if in_triangle(u, v, (.38, .2), (.38, .62), (.73, .41)):
        return (244, 244, 246, 255)
    if in_round_rect(u, v, .18, .72, .82, .82, .05):
        return (*gradient((u - .18) / .64), 255)
    return (*BG, 255)


def render(size):
    n = size * SUPERSAMPLE
    rows = []
    for py in range(size):
        row = bytearray([0])  # filtro PNG "None"
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    r, g, b, a = color_at((px * SUPERSAMPLE + sx + .5) / n, (py * SUPERSAMPLE + sy + .5) / n)
                    acc[0] += r * a; acc[1] += g * a; acc[2] += b * a; acc[3] += a
            samples = SUPERSAMPLE ** 2
            alpha = acc[3] / samples
            rgb = [round(c / acc[3]) if acc[3] else 0 for c in acc[:3]]
            row += bytes([*rgb, round(alpha)])
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def ico(images):
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    body = b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return out + body


if __name__ == "__main__":
    here = Path(__file__).parent
    images = [(s, render(s)) for s in SIZES]
    (here / "gamedock.ico").write_bytes(ico(images))
    (here / "gamedock.png").write_bytes(images[-1][1])
    print("assets/gamedock.ico gerado")
