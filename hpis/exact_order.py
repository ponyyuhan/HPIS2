"""Canonical ordering of cosine neighbours for the stored float32 coordinates."""

from functools import cmp_to_key

import numpy as np

_DOUBLE_EPS = np.finfo(np.float64).eps


def order_margin(dim):
    """An absolute allowance for two FP64 normalized squared-chord distances.

    Each squared length and the final sum use at most dim FP64 additions; all
    normalized vectors have length one and all squared chord distances are
    bounded by four. This conservative allowance only selects pairs for exact
    comparison. It never declares two different cosine scores equal.
    """
    return float(64 * (dim + 4) * _DOUBLE_EPS)


def _integer_coordinates(values):
    # Every finite binary32 coordinate is an exact dyadic rational. Scaling a
    # vector by any common positive denominator preserves its direction.
    ratios = [float(x).as_integer_ratio() for x in np.asarray(values, dtype=np.float32)]
    common = max(den for _, den in ratios)
    return [num * (common // den) for num, den in ratios]


def _compare(a, b):
    dot_a, norm_a, id_a, _ = a
    dot_b, norm_b, id_b, _ = b
    if (dot_a < 0) != (dot_b < 0):
        return 1 if dot_a < 0 else -1
    # The query norm cancels. Compare |dot|/sqrt(norm_squared) exactly,
    # reversing the ordering of magnitudes when both inner products are negative.
    left = dot_a * dot_a * norm_b
    right = dot_b * dot_b * norm_a
    if left != right:
        return -1 if ((left > right) != (dot_a < 0)) else 1
    return (id_a > id_b) - (id_a < id_b)


def exact_nearest(ids, distances, k, query, rows):
    """Return positions in canonical cosine order, breaking actual ties by ID.

    distances are FP64 squared chord distances for the canonical float32 data.
    rows is either an array or a callable yielding one float32 vector by local
    position. Well-separated inputs never touch rows. At the k boundary the
    shortlist includes every score whose numerical intervals can overlap.
    """
    n = len(ids)
    count = min(k, n)
    if count == 0:
        return np.empty(0, dtype=np.intp)
    ids = np.asarray(ids)
    distances = np.asarray(distances)
    margin = order_margin(len(query))
    if count < n:
        cutoff = np.partition(distances, count - 1)[count - 1]
        candidates = np.flatnonzero(distances <= cutoff + margin)
    else:
        candidates = np.arange(n, dtype=np.intp)
    order = candidates[np.lexsort((ids[candidates], distances[candidates]))]
    if len(order) < 2:
        return order[:count]
    near = np.flatnonzero(np.diff(distances[order]) <= margin)
    if not len(near):
        return order[:count]
    get_row = rows if callable(rows) else rows.__getitem__
    q = _integer_coordinates(query)
    # Connected runs of overlapping intervals form disjoint sort groups.
    for run in np.split(near, np.flatnonzero(np.diff(near) != 1) + 1):
        start, stop = int(run[0]), int(run[-1]) + 2
        if start >= count:
            break
        scored = []
        for pos in order[start:stop]:
            v = _integer_coordinates(get_row(int(pos)))
            norm = sum(x * x for x in v)
            if norm == 0:
                raise ValueError("cosine distance requires a nonzero vector")
            dot = sum(x * y for x, y in zip(v, q, strict=True))
            scored.append((dot, norm, int(ids[pos]), pos))
        scored.sort(key=cmp_to_key(_compare))
        order[start:stop] = [r[3] for r in scored]
    return order[:count]
