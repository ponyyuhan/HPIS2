"""Paired statistics for interleaved plaintext/ciphertext requests.

Every measured position yields one plaintext and one ciphertext latency for the same
input, issued back to back in a balanced (AB/BA) random order. Uncertainty comes from
a circular moving-block bootstrap over pairs, in collection order and stratified by
round, so slow drift of the host (frequency, cache, neighbours) is resampled in blocks
instead of being treated as independent noise.
"""

import numpy as np

REPLICATES = 4000
MAX_BLOCK = 16


def _overheads(plain, cipher, axis=-1):
    return {
        "mean": 100 * (cipher.mean(axis) / plain.mean(axis) - 1),
        "p50": 100 * (np.quantile(cipher, 0.5, axis=axis) / np.quantile(plain, 0.5, axis=axis) - 1),
        "p95": 100 * (np.quantile(cipher, 0.95, axis=axis) / np.quantile(plain, 0.95, axis=axis) - 1),
        "delta_us": cipher.mean(axis) - plain.mean(axis),
    }


def paired(rounds, seed, replicates=REPLICATES):
    """rounds: list of (n_r, 2) arrays [baseline_us, other_us] in collection order."""
    rounds = [np.asarray(r, dtype=np.float64) for r in rounds if len(r)]
    both = np.concatenate(rounds)
    point = {k: float(v) for k, v in _overheads(both[:, 0], both[:, 1]).items()}
    rng = np.random.default_rng(seed)
    plans = []
    for r in rounds:
        n = len(r)
        size = min(MAX_BLOCK, max(1, int(np.sqrt(n))))
        plans.append((r, n, size, -(-n // size)))
    draws = {k: [] for k in point}
    for done in range(0, replicates, 250):
        b = min(250, replicates - done)
        parts = []
        for r, n, size, blocks in plans:
            starts = rng.integers(0, n, size=(b, blocks))
            idx = ((starts[:, :, None] + np.arange(size)) % n).reshape(b, -1)[:, :n]
            parts.append(r[idx])
        sample = np.concatenate(parts, axis=1)
        for k, v in _overheads(sample[..., 0], sample[..., 1], axis=1).items():
            draws[k].append(v)
    ci = {k: [float(x) for x in np.percentile(np.concatenate(v), [2.5, 97.5])] for k, v in draws.items()}
    per_round = [float(100 * (r[:, 1].mean() / r[:, 0].mean() - 1)) for r in rounds]
    return {
        "pairs": int(len(both)),
        "baseline_mean_us": float(both[:, 0].mean()),
        "other_mean_us": float(both[:, 1].mean()),
        "overhead_pct": {k: point[k] for k in ("mean", "p50", "p95")},
        "ci95_pct": {k: ci[k] for k in ("mean", "p50", "p95")},
        "delta_mean_us": point["delta_us"],
        "delta_mean_us_ci95": ci["delta_us"],
        "per_round_overhead_pct": per_round,
        "method": (
            f"paired circular moving-block bootstrap, blocks of <= {MAX_BLOCK} consecutive "
            f"pairs, stratified by round, {replicates} replicates, percentile 95% interval"
        ),
    }


def describe(values):
    v = np.asarray(values, dtype=np.float64)
    return {
        "n": int(len(v)),
        "mean": float(v.mean()),
        "p50": float(np.quantile(v, 0.5)),
        "p95": float(np.quantile(v, 0.95)),
        "p99": float(np.quantile(v, 0.99)),
    }
