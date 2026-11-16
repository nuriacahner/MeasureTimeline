"""Draw the toolbar / timeline icons (an angle with an arc and a tick). Pure Python PNG.

    py -3.10 tools/make_icons.py
"""

import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'resources', 'MeasureTimeline')
INK = (60, 60, 60)
BLUE = (30, 140, 255)


def png(path, size, px):
    raw = b''.join(b'\x00' + bytes(c for p in row for c in p) for row in px)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n')
        fh.write(chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)))
        fh.write(chunk(b'IDAT', zlib.compress(raw, 9)))
        fh.write(chunk(b'IEND', b''))


def seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def draw(size):
    ss = 4
    n = size * ss
    w = max(1.2, n / 14.0)
    ox, oy = n * 0.15, n * 0.85                       # vertex of the angle
    ex1 = (n * 0.92, n * 0.85)                        # horizontal arm
    ang = math.radians(50)
    ex2 = (ox + n * 0.8 * math.cos(ang), oy - n * 0.8 * math.sin(ang))
    r = n * 0.5
    cov = [[None] * n for _ in range(n)]
    for y in range(n):
        for x in range(n):
            px, py = x + 0.5, y + 0.5
            if min(seg_dist(px, py, ox, oy, *ex1), seg_dist(px, py, ox, oy, *ex2)) < w / 2:
                cov[y][x] = INK
            dx, dy = px - ox, oy - py
            a = math.atan2(dy, dx)
            if 0 <= a <= ang and abs(math.hypot(dx, dy) - r) < w / 2:
                cov[y][x] = BLUE
    px_rows = []
    for y in range(size):
        row = []
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = cov[y * ss + sy][x * ss + sx]
                    if c:
                        acc[0] += c[0]
                        acc[1] += c[1]
                        acc[2] += c[2]
                        acc[3] += 1
            k = acc[3]
            row.append((acc[0] // k, acc[1] // k, acc[2] // k, 255 * k // (ss * ss))
                       if k else (0, 0, 0, 0))
        px_rows.append(row)
    return px_rows


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for s in (16, 32, 64):
        png(os.path.join(OUT, '%dx%d.png' % (s, s)), s, draw(s))
    print('icons written to', OUT)
