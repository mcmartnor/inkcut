"""Packing and editing geometry in layout units, with no Qt dependency."""


def pack_shelf(sizes, area_w, area_h, spacing):
    """NFDH positions in input order; zero-sized line dimensions are allowed."""
    if area_w <= 0 or area_h <= 0 or spacing < 0:
        raise ValueError('Area must be positive and spacing nonnegative')
    if any(w < 0 or h < 0 for w, h in sizes):
        raise ValueError('Sizes must be nonnegative')
    positions = [None] * len(sizes)
    x = y = shelf_h = 0.0
    occupied = False
    for i in sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0], i)):
        w, h = sizes[i]
        if w > area_w:
            continue
        nx, ny, nh = x, y, shelf_h
        if occupied and x + w > area_w:
            nx, ny, nh = 0.0, y + shelf_h + spacing, 0.0
        if ny + h > area_h:
            continue
        positions[i] = (nx, ny)
        x, y, shelf_h = nx + w + spacing, ny, max(nh, h)
        occupied = True
    return positions, [i for i, p in enumerate(positions) if p is None]


def pack_skyline(sizes, area_w, area_h, spacing, rotated_sizes=None):
    """Bottom-left skyline; spacing occupies the profile, not the sheet edge."""
    if area_w <= 0 or area_h <= 0 or spacing < 0:
        raise ValueError('Area must be positive and spacing nonnegative')
    if rotated_sizes is not None and len(rotated_sizes) != len(sizes):
        raise ValueError('Rotated sizes must match sizes')
    if any(w < 0 or h < 0 for w, h in list(sizes) + list(rotated_sizes or [])):
        raise ValueError('Sizes must be nonnegative')
    positions = [None] * len(sizes)
    skyline = [(0.0, area_w + spacing, 0.0)]
    order = sorted(range(len(sizes)),
                   key=lambda i: (-sizes[i][0] * sizes[i][1], -max(sizes[i]), i))
    for i in order:
        choices = []
        orientations = [(False, sizes[i])]
        if rotated_sizes is not None:
            orientations.append((True, rotated_sizes[i]))
        for rotated, (w, h) in orientations:
            for x, _, level in skyline:
                if x + w > area_w:
                    continue
                y = max((z for a, b, z in skyline
                         if a < x + w + spacing and b > x), default=level)
                if y + h <= area_h:
                    choices.append((y + h, x, rotated, y, w, h))
        if not choices:
            continue
        _, x, rotated, y, w, h = min(choices)
        positions[i] = (x, y, rotated)
        end = x + w + spacing
        updated = []
        for a, b, z in skyline:
            if b <= x or a >= end:
                updated.append((a, b, z))
            else:
                if a < x:
                    updated.append((a, x, z))
                if b > end:
                    updated.append((end, b, z))
        if end > x:
            updated.append((x, end, y + h + spacing))
        skyline = []
        for a, b, z in sorted(updated):
            if skyline and skyline[-1][1] == a and skyline[-1][2] == z:
                skyline[-1] = (skyline[-1][0], b, z)
            else:
                skyline.append((a, b, z))
    return positions, [i for i, p in enumerate(positions) if p is None]


def pack_best(sizes, area_w, area_h, spacing, rotated_sizes=None):
    skyline = pack_skyline(sizes, area_w, area_h, spacing, rotated_sizes)
    positions, overflow = pack_shelf(sizes, area_w, area_h, spacing)
    shelf = ([(p[0], p[1], False) if p is not None else None
              for p in positions], overflow)

    def score(result):
        height = max((p[1] + (rotated_sizes[i] if p[2] else sizes[i])[1]
                      for i, p in enumerate(result[0]) if p is not None), default=0)
        return len(result[1]), height

    return shelf if score(shelf) < score(skyline) else skyline


def snap_value(v, candidates, tol):
    hits = [c for c in candidates if abs(c - v) <= tol]
    return min(hits, key=lambda c: (abs(c - v), c)) if hits else v


def snap_rect(x, y, w, h, grid, edges_x, edges_y, tol):
    def axis(v, size, edges):
        hits = [(abs(c - (v + offset)), c, offset, c - offset)
                for c in edges for offset in (0, size / 2, size)
                if abs(c - (v + offset)) <= tol]
        if hits:
            return min(hits)[3]
        return round(v / grid) * grid if grid > 0 else v
    return axis(x, w, edges_x), axis(y, h, edges_y)


def hit_test(point, rects):
    x, y = point
    for i in range(len(rects) - 1, -1, -1):
        rx, ry, w, h = rects[i]
        if rx <= x <= rx + w and ry <= y <= ry + h:
            return i
    return None


def invert_affine(t):
    a, b, c, d, x, y = t
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    return (d / det, -b / det, -c / det, a / det,
            (c * y - d * x) / det, (b * x - a * y) / det)


def affine_from_points(src, dst):
    """Three point pairs to QTransform-order affine coefficients."""
    (x0, y0), (x1, y1), (x2, y2) = src
    sx, sy, tx, ty = x1 - x0, y1 - y0, x2 - x0, y2 - y0
    det = sx * ty - sy * tx
    if det == 0:
        raise ValueError('Source points must not be collinear')
    inv = (ty / det, -sy / det, -tx / det, sx / det,
           (tx * y0 - ty * x0) / det, (sy * x0 - sx * y0) / det)
    (u0, v0), (u1, v1), (u2, v2) = dst
    a, b, c, d, x, y = inv
    ux, uy, vx, vy = u1 - u0, u2 - u0, v1 - v0, v2 - v0
    return (ux * a + uy * b, vx * a + vy * b,
            ux * c + uy * d, vx * c + vy * d,
            ux * x + uy * y + u0, vx * x + vy * y + v0)
