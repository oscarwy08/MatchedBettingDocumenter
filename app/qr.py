"""Offline QR SVG for the phone link. Byte mode, ECC M, versions 1–6."""

from __future__ import annotations

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 256:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]

# version: (size, data codewords, ecc codewords)
_VERSIONS = {
    1: (21, 16, 10),
    2: (25, 28, 16),
    3: (29, 44, 26),
    4: (33, 64, 36),
    5: (37, 86, 48),
    6: (41, 108, 64),
}
_ALIGN = {1: (), 2: (18,), 3: (22,), 4: (26,), 5: (30,), 6: (34,)}
_REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7}


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _poly_mul(p: list[int], q: list[int]) -> list[int]:
    out = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            out[i + j] ^= _gf_mul(a, b)
    return out


def _rs_generator(degree: int) -> list[int]:
    g = [1]
    for i in range(degree):
        g = _poly_mul(g, [1, _EXP[i]])
    return g


def _rs_encode(data: list[int], ecc_len: int) -> list[int]:
    gen = _rs_generator(ecc_len)
    out = data + [0] * ecc_len
    for i in range(len(data)):
        coef = out[i]
        if coef == 0:
            continue
        for j, g in enumerate(gen):
            out[i + j] ^= _gf_mul(g, coef)
    return out[-ecc_len:]


def _bits(value: int, width: int) -> list[int]:
    return [(value >> i) & 1 for i in range(width - 1, -1, -1)]


def _encode_bytes(data: bytes, data_cw: int) -> list[int]:
    bits: list[int] = [0, 1, 0, 0]
    bits += _bits(len(data), 8)
    for byte in data:
        bits += _bits(byte, 8)
    bits += [0, 0, 0, 0]
    while len(bits) % 8:
        bits.append(0)
    pad = [0xEC, 0x11]
    values: list[int] = []
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i : i + 8]:
            byte = (byte << 1) | bit
        values.append(byte)
    n = 0
    while len(values) < data_cw:
        values.append(pad[n % 2])
        n += 1
    return values[:data_cw]


def _is_finder(r: int, c: int, size: int) -> bool:
    return (
        (r < 8 and c < 8)
        or (r < 8 and c >= size - 8)
        or (r >= size - 8 and c < 8)
    )


def _reserved(r: int, c: int, size: int, centers: tuple[int, ...]) -> bool:
    if _is_finder(r, c, size):
        return True
    if r == 6 or c == 6:
        return True
    if r == 8 and (c <= 8 or c >= size - 8):
        return True
    if c == 8 and (r <= 8 or r >= size - 8):
        return True
    for ar in centers:
        for ac in centers:
            if (ar < 10 and ac < 10) or (ar < 10 and ac > size - 10) or (ar > size - 10 and ac < 10):
                continue
            if abs(r - ar) <= 2 and abs(c - ac) <= 2:
                return True
    return False


def _place_finders(grid: list[list[int | None]], size: int) -> None:
    def draw(sr: int, sc: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = sr + r, sc + c
                if 0 <= rr < size and 0 <= cc < size:
                    grid[rr][cc] = 0
        for r in range(7):
            for c in range(7):
                outer = r in (0, 6) or c in (0, 6)
                inner = 2 <= r <= 4 and 2 <= c <= 4
                grid[sr + r][sc + c] = 1 if outer or inner else 0

    draw(0, 0)
    draw(0, size - 7)
    draw(size - 7, 0)


def _place_timing(grid: list[list[int | None]], size: int) -> None:
    for i in range(size):
        if grid[6][i] is None:
            grid[6][i] = 1 if i % 2 == 0 else 0
        if grid[i][6] is None:
            grid[i][6] = 1 if i % 2 == 0 else 0


def _place_align(grid: list[list[int | None]], centers: tuple[int, ...], size: int) -> None:
    for ar in centers:
        for ac in centers:
            if (ar < 10 and ac < 10) or (ar < 10 and ac > size - 10) or (ar > size - 10 and ac < 10):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    grid[ar + r][ac + c] = 1 if max(abs(r), abs(c)) in (0, 2) else 0


def _zigzag(size: int, centers: tuple[int, ...]):
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not _reserved(row, c, size, centers):
                    yield row, c
        upward = not upward
        col -= 2


def _mask_fn(kind: int):
    fns = (
        lambda r, c: (r + c) % 2 == 0,
        lambda r, c: r % 2 == 0,
        lambda r, c: c % 3 == 0,
        lambda r, c: (r + c) % 3 == 0,
        lambda r, c: (r // 2 + c // 3) % 2 == 0,
        lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
        lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
        lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
    )
    return fns[kind]


def _format_bits(mask: int) -> list[int]:
    data = mask  # ECC M = 00, so just the 3 mask bits
    payload = data
    rem = payload << 10
    gen = 0b10100110111
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= gen << (i - 10)
    bits = (payload << 10 | rem) ^ 0x5412
    return _bits(bits, 15)


def _place_format(grid: list[list[int]], size: int, mask: int) -> None:
    bits = _format_bits(mask)
    coords = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    for bit, (r, c) in zip(bits, coords):
        grid[r][c] = bit
    other = [
        (size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8), (size - 5, 8),
        (size - 6, 8), (size - 7, 8), (8, size - 8), (8, size - 7), (8, size - 6),
        (8, size - 5), (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1),
    ]
    for bit, (r, c) in zip(bits, other):
        grid[r][c] = bit
    grid[size - 8][8] = 1


def _penalty(grid: list[list[int]], size: int) -> int:
    score = 0
    for r in range(size):
        run = 1
        for c in range(1, size):
            if grid[r][c] == grid[r][c - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    for c in range(size):
        run = 1
        for r in range(1, size):
            if grid[r][c] == grid[r - 1][c]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(size - 1):
        for c in range(size - 1):
            if grid[r][c] == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]:
                score += 3
    dark = sum(sum(row) for row in grid)
    percent = (dark * 100) // (size * size)
    score += abs(percent - 50) // 5 * 10
    return score


def _build(text: str) -> list[list[int]]:
    raw = text.encode("utf-8")
    version = 1
    for ver, (_size, data_cw, _ecc) in _VERSIONS.items():
        if len(raw) + 2 <= data_cw:
            version = ver
            break
    else:
        raw = raw[: _VERSIONS[6][1] - 2]
        version = 6
    size, data_cw, ecc_cw = _VERSIONS[version]
    centers = _ALIGN[version]
    data = _encode_bytes(raw, data_cw)
    ecc = _rs_encode(data, ecc_cw)
    bits: list[int] = []
    for byte in data + ecc:
        bits += _bits(byte, 8)
    bits += [0] * _REMAINDER[version]

    best = None
    best_score = 10**9
    for mask in range(8):
        grid: list[list[int | None]] = [[None] * size for _ in range(size)]
        _place_finders(grid, size)
        _place_align(grid, centers, size)
        _place_timing(grid, size)
        grid[8][size - 8] = 1
        fn = _mask_fn(mask)
        i = 0
        for r, c in _zigzag(size, centers):
            bit = bits[i] if i < len(bits) else 0
            if fn(r, c):
                bit ^= 1
            grid[r][c] = bit
            i += 1
        filled = [[0 if cell is None else cell for cell in row] for row in grid]
        _place_format(filled, size, mask)
        score = _penalty(filled, size)
        if score < best_score:
            best_score = score
            best = filled
    assert best is not None
    return best


def qr_svg(text: str, *, module: int = 8, quiet: int = 4) -> str:
    grid = _build(text)
    size = len(grid)
    dim = (size + quiet * 2) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" width="180" height="180" class="phone-qr" role="img" aria-label="QR code">'
        f'<rect width="{dim}" height="{dim}" fill="#fffdf8"/>'
    ]
    for r, row in enumerate(grid):
        for c, bit in enumerate(row):
            if not bit:
                continue
            x = (c + quiet) * module
            y = (r + quiet) * module
            parts.append(f'<rect x="{x}" y="{y}" width="{module}" height="{module}" fill="#1c1913"/>')
    parts.append("</svg>")
    return "".join(parts)
