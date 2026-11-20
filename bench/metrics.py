"""Descriptive timing statistics and uncertainty for randomized paired requests."""

import math

import numpy as np


def measured(values):
    return {
        "mean": float(np.mean(values)),
        "p50": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
    }


def percent(secure, plain):
    return 100 * (secure / plain - 1)


def paired_intervals(pairs, rng):
    """Resample contiguous blocks of AB/BA pairs within each measurement round."""
    repetitions = 2000
    chunks = []
    sizes = []
    for round_values in pairs:
        n = len(round_values)
        size = min(16, max(1, int(np.sqrt(n))))
        starts = rng.integers(0, n, size=(repetitions, math.ceil(n / size)))
        indices = (starts[:, :, None] + np.arange(size)) % n
        chunks.append(round_values[indices.reshape(repetitions, -1)[:, :n]])
        sizes.append(size)
    observations = np.concatenate(chunks, axis=1)
    summaries = {
        "mean": observations.mean(axis=1),
        "p50": np.quantile(observations, 0.5, axis=1),
        "p95": np.quantile(observations, 0.95, axis=1),
    }
    ci = {}
    for key, both in summaries.items():
        ci[key] = np.percentile(percent(both[:, 1], both[:, 0]), [2.5, 97.5]).tolist()
    return {
        "method": "paired circular moving blocks in collection order, stratified by round",
        "confidence": 0.95,
        "replicates": repetitions,
        "block_pairs_per_round": sizes,
        "overhead_pct": ci,
    }


def aggregate(samples, conditions, seed):
    rng = np.random.default_rng(seed)
    report = {}
    for kind in conditions:
        data = [sample for sample in samples if sample["kind"] == kind]
        protected = next(s["mode"] for s in data if s["mode"] != "plain")
        modes = {}
        for mode in ("plain", protected):
            items = [s for s in data if s["mode"] == mode]
            keys = sorted({key for s in items for key in s["stats"]})
            modes[mode] = {
                "queries": len(items),
                "e2e_us": measured([i["us"] for i in items]),
                "sdk_us": measured([i["sdk_us"] for i in items]),
                "recall": float(np.mean([i["recall"] for i in items])),
                "exact_order": float(np.mean([i["exact_order"] for i in items])),
                "average": {
                    k: float(np.mean([i["stats"].get(k, 0) for i in items]))
                    for k in keys
                },
            }
        pairs, by_round = [], []
        for rnd in sorted({s["round"] for s in data}):
            items = [s for s in data if s["round"] == rnd]
            values = {}
            for s in items:
                values.setdefault(s["position"], {})[s["mode"]] = s["us"]
            ordered = np.array(
                [[p["plain"], p[protected]] for _, p in sorted(values.items())]
            )
            pairs.append(ordered)
            by_round.append(float(percent(ordered[:, 1].mean(), ordered[:, 0].mean())))
        modes[protected]["overhead_pct"] = {
            key: float(percent(val, modes["plain"]["e2e_us"][key]))
            for key, val in modes[protected]["e2e_us"].items()
        }
        modes[protected]["round_mean_overhead_pct"] = by_round
        modes[protected]["paired_interval"] = paired_intervals(pairs, rng)
        report[kind] = modes
    return report
