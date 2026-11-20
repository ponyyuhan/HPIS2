"""Paired exact retrieval on the same prepared encrypted and plaintext databases."""

import argparse
import asyncio
import gzip
import json
import random
from pathlib import Path
from time import perf_counter_ns

import numpy as np
from threadpoolctl import threadpool_limits

from bench.cluster import fleet
from bench.metrics import aggregate
from bench.run import load_data, machine, oracle, validate_response
from hpis.sdk import Device

CONDITIONS = ("global", "filtered10", "filtered20")


async def sample(endpoints, base, queries, warm, truth, rounds, seed, conditions):
    rng = random.Random(seed)
    samples = []
    async with (
        Device(*endpoints["plain"]) as plain,
        Device(*endpoints["encrypted"]) as secure,
    ):

        async def issue(device, q, groups):
            return await device.vector(
                q,
                10,
                refine=device.mode == "encrypted",
                predicates=[["word", f"g{g}"] for g in groups],
                combine="any",
            )

        for rnd in range(rounds):
            blocks = list(conditions)
            rng.shuffle(blocks)
            for kind in blocks:
                for index, q in enumerate(warm):
                    groups = [] if kind == "global" else [index]
                    if kind == "filtered20":
                        groups.append(index + 1)
                    for d in (plain, secure) if index % 2 == 0 else (secure, plain):
                        await issue(d, q, groups)
                order = list(range(len(queries)))
                rng.shuffle(order)
                for position, qi in enumerate(order):
                    q = queries[qi]
                    groups = []
                    if kind == "global":
                        expected = truth[qi]
                    else:
                        group_count = 1 if kind == "filtered10" else 2
                        groups = rng.sample(range(len(base) // 10), group_count)
                        eligible = np.sort(
                            np.concatenate(
                                [np.arange(g * 10, g * 10 + 10) for g in groups]
                            )
                        )
                        local_order = oracle(base[eligible], queries[qi : qi + 1], 10)[
                            0
                        ]
                        expected = eligible[local_order].tolist()
                    pair = (
                        (plain, secure)
                        if (position + rnd) % 2 == 0
                        else (secure, plain)
                    )
                    for seq, device in enumerate(pair):
                        start = perf_counter_ns()
                        result = await issue(device, q, groups)
                        sdk_us = (perf_counter_ns() - start) / 1000
                        ids = validate_response(result, len(expected))
                        us = (perf_counter_ns() - start) / 1000
                        if ids != expected:
                            raise RuntimeError(
                                f"Incorrect ordered result in {kind}, query {qi}"
                            )
                        samples.append(
                            dict(
                                kind=kind,
                                mode=device.mode,
                                round=rnd,
                                position=position,
                                query=qi,
                                groups=groups,
                                order=seq,
                                us=us,
                                sdk_us=sdk_us,
                                ids=ids,
                                gold=expected,
                                recall=1.0,
                                exact_order=True,
                                stats=result["stats"],
                            )
                        )
            print(f"Finished round {rnd + 1}/{rounds}.", flush=True)
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, required=True, help="existing 'bench.run prepare' output"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--queries", type=int, default=512)
    parser.add_argument("--query-start", type=int, default=512)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261120)
    parser.add_argument(
        "--conditions", choices=CONDITIONS, nargs="+", default=list(CONDITIONS)
    )
    args = parser.parse_args()
    info = json.loads((args.root / "clients" / "deployment.json").read_text())
    rows = info["dataset"]["rows"]
    if (
        rows < 20
        or args.rounds < 1
        or args.queries < 1
        or len(set(args.conditions)) != len(args.conditions)
    ):
        parser.error("invalid corpus size, rounds, query count, or repeated condition")
    base, q, warm, shape = load_data(
        args.dataset, rows, args.queries, 8, args.query_start
    )
    with threadpool_limits(1, user_api="blas"):
        truth = oracle(base, q, 10)
        with fleet(args.root, existing=True, modes=("plain", "encrypted")) as urls:
            samples = asyncio.run(
                sample(
                    urls, base, q, warm, truth, args.rounds, args.seed, args.conditions
                )
            )
        report = dict(
            environment=machine(),
            dataset=shape,
            parameters=dict(
                rows=rows,
                queries=args.queries,
                query_start=args.query_start,
                k=10,
                rounds=args.rounds,
                seed=args.seed,
                beta=info["beta"],
            ),
            boundary="terminal SDK through two authenticated services and validated record bodies; protected refinement receives all publicly selected metadata before completing",
            conditions={
                "global": "search all vectors",
                "filtered10": "one posting list, ten eligible records and k=10",
                "filtered20": "union of two posting lists, twenty eligible records and k=10",
            },
            results=aggregate(samples, args.conditions, args.seed),
        )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    with gzip.open(args.out / "samples.jsonl.gz", "wt") as stream:
        for s in samples:
            stream.write(json.dumps(s, separators=(",", ":")) + "\n")
    for kind, m in report["results"].items():
        print(
            kind,
            {k: round(m[k]["e2e_us"]["mean"], 1) for k in ("plain", "encrypted")},
            round(m["encrypted"]["overhead_pct"]["mean"], 2),
            flush=True,
        )


if __name__ == "__main__":
    main()
