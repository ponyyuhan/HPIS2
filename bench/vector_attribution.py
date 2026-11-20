"""Controlled GloVe crossover for the HPIS rapid-vector timing claim.

Only the disposable benchmark server enables FP32 directional scoring for a
plaintext request. Both fast arms then run the production PPE ranking branch.
Run as python -m bench.vector_attribution --help.
"""

import argparse
import asyncio
import gzip
import itertools
import json
import multiprocessing as mp
import os
import random
import socket
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from time import perf_counter_ns as clock

import numpy as np
from threadpoolctl import threadpool_limits

from bench.cluster import free_port
from bench.metrics import measured, paired_intervals, percent
from bench.run import load_data, machine, oracle, record, validate_response
from hpis import services
from hpis.codec import nearest, pack, unit_vectors, unpack
from hpis.provision import enroll, init
from hpis.sdk import Device
from hpis.store import Store, cosine_distances

ARMS = ("plain_exact", "plain_fast", "ppe_fast")


def inv_norms(data):
    lengths = np.sqrt(np.einsum("ij,ij->i", data, data))
    return 1 / np.where(lengths > 0, lengths, 1.0)


class AttributionStore(Store):
    """Use the same hot plaintext array and same PPE kernel for the matched arm."""

    def search(self, aid, mode, dim, beta, op):
        # In production, refine has no effect on plain mode. In this process only
        # it selects its ordinary FP64-corrected branch or the matched FP32 branch.
        self.plain_fast = mode == "plain" and not op.get("refine", False)
        self.sql_us = 0.0
        response = super().search(aid, mode, dim, beta, op)
        response["stats"]["sql_body_us"] = self.sql_us
        return response

    def matrix(self, aid, dim, mode, key):
        matrix = super().matrix(aid, dim, mode, key)
        if self.plain_fast and mode == "plain":
            if getattr(self, "_plain_base", None) is not matrix:
                self._plain_base = matrix
                self._plain_directional = replace(
                    matrix, inverse_norms=inv_norms(matrix.sketches)
                )
            return self._plain_directional
        return matrix

    def read_items(self, aid, ids):
        start = clock()
        rows = super().read_items(aid, ids)
        self.sql_us = (clock() - start) / 1000
        return rows


class TimedDevice(Device):
    async def request(self, op, grant):
        self.ready_at = clock()
        return await super().request(op, grant)

    def envelope(self, op, grant):
        start = clock()
        message = super().envelope(op, grant)
        self.envelope_us = (clock() - start) / 1000
        return message


def server(role, folder, port, upstream):
    if role == "hpis":
        services.Store = AttributionStore
    with threadpool_limits(1, user_api="blas"):
        services.serve(role, folder, "127.0.0.1", port, upstream)


@contextmanager
def cluster(root, dim, beta):
    plain_dir = init(root, "plain", dim, beta)
    # Two tenants on exactly one storage DB, gateway, certificate pair and CPU.
    public_file = root / "public.msgpack"
    config = unpack(public_file.read_bytes())
    config["mode"] = "encrypted"
    public_file.write_bytes(pack(config))
    encrypted_dir = enroll(root, "ppe")
    children = []
    url = None
    try:
        for role in ("hpis", "hpic"):
            port = free_port()
            proc = mp.get_context("spawn").Process(
                target=server, args=(role, root / role, port, url)
            )
            proc.start()
            children.append(proc)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if not proc.is_alive():
                    raise RuntimeError(f"{role} exited with {proc.exitcode}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.03)
            else:
                raise RuntimeError(f"{role} did not start")
            url = f"https://127.0.0.1:{port}"
        yield plain_dir, encrypted_dir, url
    finally:
        for proc in reversed(children):
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join()


def crossover(arms, size, rng):
    """Balance every possible call order in consecutive groups, then shuffle IDs."""
    ids = list(range(size))
    rng.shuffle(ids)
    orders = []
    while len(orders) < size:
        cycle = list(itertools.permutations(arms))
        rng.shuffle(cycle)
        orders.extend(cycle)
    return zip(ids, orders)


async def experiment(devices, base, queries, warm, truth, args):
    p, e = devices
    e.session = p.session
    # Interleave writes so both tenants' rows are introduced in comparable chunks.
    for offset in range(0, len(base), 128):
        rows = [record(i, base[i]) for i in range(offset, min(offset + 128, len(base)))]
        for dev in (p, e) if (offset // 128) % 2 == 0 else (e, p):
            await dev.put(rows)
    rng = random.Random(args.seed)
    samples = []

    async def run(arm, vector):
        dev = e if arm == "ppe_fast" else p
        start = clock()
        response = await dev.vector(vector, args.k, refine=arm == "plain_exact")
        ids = validate_response(response, args.k)
        us = (clock() - start) / 1000
        s = response["stats"]
        phases = {
            "prepare_target": (dev.ready_at - start) / 1000,
            "sign_seal": dev.envelope_us,
            "index_including_validation": s["store_us"] - s["sql_body_us"],
            "sql_body": s["sql_body_us"],
            "hpic_open_unpack": s["open_and_filter_us"],
        }
        phases["other_service_transport_and_checks"] = us - sum(phases.values())
        return ids, us, phases

    for rnd in range(args.rounds):
        for qi, order in crossover(ARMS, len(warm), rng):
            for arm in order:
                await run(arm, warm[qi])
        for pos, (qi, order) in enumerate(crossover(ARMS, len(queries), rng)):
            for slot, arm in enumerate(order):
                ids, us, phases = await run(arm, queries[qi])
                if arm == "plain_exact" and ids != truth[qi]:
                    raise RuntimeError(
                        f"plain baseline disagrees with exhaustive oracle at test row {qi}"
                    )
                samples.append(
                    dict(
                        arm=arm,
                        round=rnd,
                        position=pos,
                        slot=slot,
                        query=qi,
                        ids=ids,
                        gold=truth[qi],
                        us=us,
                        phases=phases,
                        recall=len(set(ids) & set(truth[qi])) / args.k,
                        exact_order=ids == truth[qi],
                    )
                )
        print(f"TLS crossover {rnd + 1}/{args.rounds}", flush=True)
    e.session = None
    return samples


def kernel(data, inverse, ids, q, k, arm):
    """Time only the operations used in Store.search at warm steady state."""
    t0 = clock()
    dots = data @ q
    t1 = clock()
    if arm == "plain_exact":
        # Identical cutoff/tie margin and float64 function as the original Store.
        n = len(dots)
        if n > k:
            kth = np.partition(dots, n - k)[-k]
            selected = np.flatnonzero(
                dots >= kth - 8 * data.shape[1] * np.finfo(np.float32).eps
            )
            subset, candidates = data[selected], ids[selected]
        else:
            subset, candidates = data, ids
        t2 = clock()
        ranks = cosine_distances(subset, q)
    else:
        candidates = ids
        t2 = t1
        # Plain unit-dot is an additional minimal numeric control, distinct
        # from the two matched arms with the same precomputed inverse norms.
        ranks = -dots if arm == "plain_unit_dot" else -dots * inverse
    t3 = clock()
    top = candidates[nearest(candidates, ranks, k)].tolist()
    t4 = clock()
    phases = {
        name: (right - left) / 1000
        for name, left, right in (
            ("gemv", t0, t1),
            ("shortlist_and_gather", t1, t2),
            ("direction_or_f64", t2, t3),
            ("selection_and_ids", t3, t4),
        )
    }
    return top, (t4 - t0) / 1000, phases


def kernel_experiment(root, devices, queries, truth, args):
    db = Store(root / "hpis" / "store.sqlite")
    p, e = devices
    try:
        plain = db.matrix(p.aid, p.dim, "plain", None)
        enc = db.matrix(e.aid, e.dim, "encrypted", None)
        np.testing.assert_array_equal(plain.ids, enc.ids)
        norm_p = inv_norms(plain.sketches)
        norm_e = inv_norms(enc.sketches)
        # Reusing one allocation removes process and matrix-base alignment from
        # the kernel comparison. Each arm gets the same pre-timing copy and hotness.
        data = np.empty_like(plain.sketches)
        norms = np.empty_like(norm_p)
        arms = (*ARMS, "plain_unit_dot")
        rng = random.Random(args.seed + 1)
        samples = []
        for rnd in range(args.kernel_rounds):
            for pos, (qi, order) in enumerate(crossover(arms, len(queries), rng)):
                unit = unit_vectors(queries[qi], p.dim)
                target_e = e.vectors.sketch(unit)
                for slot, arm in enumerate(order):
                    cipher = arm == "ppe_fast"
                    np.copyto(data, enc.sketches if cipher else plain.sketches)
                    np.copyto(norms, norm_e if cipher else norm_p)
                    ids, us, phases = kernel(
                        data,
                        norms,
                        plain.ids,
                        target_e if cipher else unit,
                        args.k,
                        arm,
                    )
                    if arm == "plain_exact" and ids != truth[qi]:
                        raise RuntimeError(
                            "kernel trace disagrees with plaintext oracle"
                        )
                    samples.append(
                        dict(
                            arm=arm,
                            round=rnd,
                            position=pos,
                            slot=slot,
                            query=qi,
                            us=us,
                            phases=phases,
                            recall=len(set(ids) & set(truth[qi])) / args.k,
                            exact_order=ids == truth[qi],
                        )
                    )
            print(f"Kernel crossover {rnd + 1}/{args.kernel_rounds}", flush=True)
        return samples
    finally:
        db.close()


def summarize(samples, comparisons, seed):
    arms = {}
    for arm in sorted({s["arm"] for s in samples}):
        rows = [s for s in samples if s["arm"] == arm]
        arms[arm] = {
            "queries": len(rows),
            "us": measured([s["us"] for s in rows]),
            "phase_mean_us": {
                name: float(np.mean([s["phases"][name] for s in rows]))
                for name in rows[0]["phases"]
            },
            "recall": float(np.mean([s["recall"] for s in rows])),
            "exact_order": float(np.mean([s["exact_order"] for s in rows])),
        }
    pairs = {}
    indexed = {(s["round"], s["position"], s["arm"]): s["us"] for s in samples}
    rounds = sorted({s["round"] for s in samples})
    for left, right in comparisons:
        by_round = []
        for rnd in rounds:
            positions = sorted({s["position"] for s in samples if s["round"] == rnd})
            by_round.append(
                np.array(
                    [
                        [indexed[rnd, pos, left], indexed[rnd, pos, right]]
                        for pos in positions
                    ]
                )
            )
        a, b = arms[left], arms[right]
        pairs[f"{right}_vs_{left}"] = {
            "overhead_pct": {s: percent(b["us"][s], a["us"][s]) for s in a["us"]},
            "mean_diff_us": b["us"]["mean"] - a["us"]["mean"],
            "phase_delta_us": {
                s: b["phase_mean_us"][s] - a["phase_mean_us"][s]
                for s in a["phase_mean_us"]
            },
            "by_round_pct": [
                float(percent(v[:, 1].mean(), v[:, 0].mean())) for v in by_round
            ],
            "moving_block_ci": paired_intervals(by_round, np.random.default_rng(seed))[
                "overhead_pct"
            ],
        }
    return {"arms": arms, "comparisons": pairs}


def dump_samples(path, samples):
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for s in samples:
            output.write(json.dumps(s, separators=(",", ":")) + "\n")


def history(folder):
    records = {}
    for p in sorted(Path(folder).glob("*/samples.jsonl.gz")):
        with gzip.open(p, "rt") as stream:
            rows = [
                r for line in stream if (r := json.loads(line))["kind"] == "vector_fast"
            ]
        if not rows:
            continue
        stages = {}
        for arm in ("plain", "encrypted"):
            by_mode = [r for r in rows if r["mode"] == arm]
            all_time = np.mean([r["us"] for r in by_mode])
            store = np.mean([r["stats"]["store_us"] for r in by_mode])
            opened = np.mean([r["stats"]["open_and_filter_us"] for r in by_mode])
            stages[arm] = {
                "queries": len(by_mode),
                "e2e_us": float(all_time),
                "store_us": float(store),
                "hpic_open_us": float(opened),
                "residual_us": float(all_time - store - opened),
            }
        delta = {
            k: b - stages["plain"][k]
            for k, b in stages["encrypted"].items()
            if k != "queries"
        }
        records[p.parent.name] = {
            "arms": stages,
            "delta_us": delta,
            "overhead_pct": percent(
                stages["encrypted"]["e2e_us"], stages["plain"]["e2e_us"]
            ),
        }
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=20000)
    parser.add_argument("--queries", type=int, default=512)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--kernel-rounds", type=int, default=2)
    parser.add_argument("--warmups", type=int, default=24)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261120)
    parser.add_argument("--beta", type=float, default=0.25)
    parser.add_argument("--historical-results", type=Path)
    args = parser.parse_args()
    for name in (
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[name] = "1"
    args.out.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(1, user_api="blas"):
        base, qs, warm, dataset = load_data(
            args.dataset, args.rows, args.queries, args.warmups
        )
        truth = oracle(base, qs, args.k)
        with tempfile.TemporaryDirectory(prefix="hpis-ppe-attribution-") as tmp:
            root = Path(tmp)
            with cluster(root, base.shape[1], args.beta) as (pd, ed, url):
                plain, enc = TimedDevice(pd, url), TimedDevice(ed, url)

                async def run():
                    async with plain:
                        return await experiment(
                            (plain, enc), base, qs, warm, truth, args
                        )

                samples = asyncio.run(run())
            micro = kernel_experiment(root, (plain, enc), qs, truth, args)
        comparisons = [
            ("plain_exact", "ppe_fast"),
            ("plain_exact", "plain_fast"),
            ("plain_fast", "ppe_fast"),
        ]
        report = {
            "dataset": dataset,
            "environment": machine(),
            "parameters": {
                k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()
            },
            "method": "same gateway/storage processes, SQLite DB, TLS connection and input; all six orders balanced in each 6-query block; warm indexes; no query/result cache; beta fixed",
            "e2e": summarize(samples, comparisons, args.seed),
            "micro": summarize(
                micro,
                [
                    *comparisons,
                    ("plain_unit_dot", "plain_fast"),
                    ("plain_unit_dot", "ppe_fast"),
                ],
                args.seed,
            ),
            "historical_raw": history(args.historical_results)
            if args.historical_results
            else {},
        }
    dump_samples(args.out / "tls_samples.jsonl.gz", samples)
    dump_samples(args.out / "kernel_samples.jsonl.gz", micro)
    (args.out / "summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    for name, s in report["e2e"]["comparisons"].items():
        print(name, s["overhead_pct"]["mean"], s["moving_block_ci"]["mean"], flush=True)


if __name__ == "__main__":
    main()
