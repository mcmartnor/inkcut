"""Deterministic shelf packing in layout units, with no Qt dependency."""


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
