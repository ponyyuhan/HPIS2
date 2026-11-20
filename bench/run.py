"""Run paired HPIS acceptance queries through real device, HPIC and HPIS endpoints."""

import argparse
import asyncio
import gzip
import importlib.metadata
import json
import os
import platform
import random
import shutil
import sys
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter, perf_counter_ns

import h5py
import numpy as np
from threadpoolctl import threadpool_info, threadpool_limits

from bench.cluster import MODES, fleet, serve_prepared
from bench.metrics import aggregate
from hpis.sdk import Device


# Each condition gets its own plaintext observations using the normal indexed plaintext endpoint.
# A costly refinement request must never be interleaved between an approximate request and its pair.
CONDITIONS = {
    "exact": ("encrypted", "encrypted"),
    "keyword": ("encrypted", "encrypted"),
    "vector_fast": ("encrypted", "encrypted"),
    "vector_refined": ("encrypted", "refined"),
    "vector_trusted": ("trusted-process", "trusted-process"),
}


def content(i):
    return f"Reminder {i}: review item {i % 73} at the next meeting."


def record(i, vec):
    return {
        "id": i,
        "fields": {"ref": f"item-{i}"},
        "keywords": [f"g{i // 10}"],
        "vector": vec,
        "content": content(i),
    }


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def machine():
    cpu = platform.processor() or platform.machine()
    path = Path("/proc/cpuinfo")
    if path.is_file():
        attributes = {}
        for line in path.read_text().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                attributes.setdefault(key.strip(), value.strip())
        cpu = attributes.get("model name") or attributes.get("Hardware") or cpu
        if cpu == platform.machine() and "CPU implementer" in attributes:
            cpu += f" (implementer {attributes['CPU implementer']}, part {attributes.get('CPU part', 'unknown')})"
    elif sys.platform == "darwin":
        import subprocess

        name = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        )
        if name.returncode == 0:
            cpu = name.stdout.strip()
    release = {}
    if hasattr(platform, "freedesktop_os_release") and sys.platform.startswith("linux"):
        release = platform.freedesktop_os_release()
    return {
        "os": platform.platform(),
        "distribution": release.get("PRETTY_NAME"),
        "cpu": cpu,
        "cpu_count_visible": len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else os.cpu_count(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "versions": {
            p: importlib.metadata.version(p)
            for p in ("numpy", "aiohttp", "cryptography", "PyNaCl", "msgpack", "h5py")
        },
        "blas": [
            {
                k: lib.get(k)
                for k in ("internal_api", "version", "num_threads", "architecture")
            }
            for lib in threadpool_info()
        ],
    }


def load_data(path, rows, queries, warmups, query_start=0):
    with h5py.File(path, "r") as data:
        train, test = data["train"], data["test"]
        if not 1 <= rows <= len(
            train
        ) or not 0 <= query_start <= query_start + queries <= len(test):
            raise ValueError("requested sample exceeds the HDF5 train/test split")
        shape = {"train": list(train.shape), "test": list(test.shape)}
        base = train[:rows]
        end = query_start + queries
        held_out = test[query_start:end]
        warm = test[end : end + warmups]
        distance = data.attrs.get("distance")
        if isinstance(distance, bytes):
            distance = distance.decode()
    source = None
    if (
        shape == {"train": [1183514, 100], "test": [10000, 100]}
        and distance == "angular"
    ):
        source = "https://ann-benchmarks.com/glove-100-angular.hdf5"
    return (
        base,
        held_out,
        warm,
        {
            "file": path.name,
            "source": source,
            "full_shape": shape,
            "selection": "first train rows, contiguous test rows; warmups use subsequent test rows",
            "first_test_index": query_start,
            "rows": rows,
            "dimensions": base.shape[1],
            "distance": "unit-vector cosine",
        },
    )


def oracle(base, queries, k):
    """Exhaustive reference for real cosine on stored float32 data, then row ID.

    The reference independently scores *all* rows. A small cluster of scores
    whose FP64 intervals overlap is ordered independently with exact Fractions.
    This benchmark work is outside the timed requests.
    """
    from fractions import Fraction

    def stored(inputs):
        data = np.asarray(inputs, dtype=np.float64)
        if data.ndim != 2 or not np.isfinite(data).all():
            raise ValueError("expected a matrix of finite vectors")
        with np.errstate(over="ignore", under="ignore"):
            norms = np.linalg.norm(data, axis=1, keepdims=True)
        exceptional = (
            ~np.isfinite(norms) | (norms < np.sqrt(np.finfo(np.float64).tiny))
        ).ravel()
        if exceptional.any():
            data = data.copy()
            rows = data[exceptional]
            scales = np.max(np.abs(rows), axis=1, keepdims=True)
            if np.any(scales == 0):
                raise ValueError("zero vector in reference corpus")
            data[exceptional] = rows / scales
            norms[exceptional] = np.linalg.norm(
                data[exceptional], axis=1, keepdims=True
            )
        return (data / norms).astype(np.float32)

    database, test = stored(base), stored(queries)
    n = len(database)
    count = min(k, n)
    if count <= 0:
        return [[] for _ in test]
    directions = database.astype(np.float64)
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    tolerance = 64 * (database.shape[1] + 4) * np.finfo(np.float64).eps
    truth = []
    for query in test:
        q = query.astype(np.float64)
        scores = directions @ (q / np.linalg.norm(q))
        order = np.argsort(-scores, kind="stable")
        cutoff = scores[order[count - 1]]
        stop = int(np.count_nonzero(scores >= cutoff - tolerance))
        near = np.flatnonzero(-np.diff(scores[order[:stop]]) <= tolerance)
        if len(near):
            qf = [Fraction.from_float(float(value)) for value in query]

            def reference_key(pos):
                x = [Fraction.from_float(float(value)) for value in database[pos]]
                dot = sum(a * b for a, b in zip(x, qf, strict=True))
                norm_squared = sum(a * a for a in x)
                sign = (dot > 0) - (dot < 0)
                return -sign, -sign * dot * dot / norm_squared, int(pos)

            for group in np.split(near, np.flatnonzero(np.diff(near) != 1) + 1):
                begin, end = int(group[0]), int(group[-1]) + 2
                if begin >= count:
                    break
                order[begin:end] = sorted(order[begin:end], key=reference_key)
        truth.append(order[:count].tolist())
    return truth


async def populate(endpoints, root, base):
    setup = {}
    for mode, (device_dir, url) in endpoints.items():
        async with Device(device_dir, url) as dev:
            start = perf_counter()
            for offset in range(0, len(base), 128):
                await dev.put(
                    record(i, base[i])
                    for i in range(offset, min(offset + 128, len(base)))
                )
            dbdir = root / mode / "hpis"
            setup[mode] = {
                "ingest_s": perf_counter() - start,
                "sqlite_and_wal_bytes": sum(
                    p.stat().st_size
                    for p in dbdir.glob("store.sqlite*")
                    if p.name != "store.sqlite-shm"
                ),
            }
    return setup


def prepare(args):
    base, _, _, data = load_data(args.dataset, args.rows, 0, 0)
    root = args.root
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with fleet(root, existing=False, dim=base.shape[1], beta=args.beta) as endpoints:
        setup = asyncio.run(populate(endpoints, root, base))
    clients = root / "clients"
    clients.mkdir(mode=0o700)
    for mode in MODES:
        shutil.copytree(root / mode / "devices" / "default", clients / mode)
    write_json(
        clients / "deployment.json",
        {"dataset": data, "beta": args.beta, "peer": machine(), "setup": setup},
    )
    print(
        f"Prepared {len(base)} records per mode. Benchmark client files: {clients}",
        flush=True,
    )


async def call(dev, condition, vector, rid, k):
    if condition == "exact":
        return await dev.terms([["field", "ref", f"item-{rid}"]])
    if condition == "keyword":
        return await dev.terms([["word", f"g{rid // 10}"]])
    return await dev.vector(vector, k, refine=condition == "vector_refined")


def expected(condition, rid, truth, rows):
    if condition == "exact":
        return [rid]
    if condition == "keyword":
        return list(range(10 * (rid // 10), min(rows, 10 * (rid // 10) + 10)))
    return truth


def validate_response(result, expected_count):
    records = result["records"]
    ids = [rid for rid, _ in records]
    if len(ids) != expected_count or len(set(ids)) != len(ids):
        raise RuntimeError("incorrect result count or duplicate record ID")
    if any(text != content(rid) for rid, text in records):
        raise RuntimeError("wrong document body")
    if result["stats"]["body_rows"] != len(ids):
        raise RuntimeError("unexpected document body read amplification")
    return ids


async def measure(args, endpoints, queries, warm, truth, rows):
    first, samples, control = {}, [], {}
    rng = random.Random(args.seed)
    async with AsyncExitStack() as stack:
        devices = {
            mode: await stack.enter_async_context(Device(*path))
            for mode, path in endpoints.items()
        }
        for mode, dev in devices.items():
            start = perf_counter_ns()
            await dev.vector(queries[0], args.k)
            first[mode] = (perf_counter_ns() - start) / 1000
        for rnd in range(args.rounds):
            condition_order = list(args.conditions)
            rng.shuffle(condition_order)
            for condition in condition_order:
                mode, label = CONDITIONS[condition]
                # Symmetric condition-specific warmup immediately precedes each independent crossover.
                for index, vec in enumerate(warm):
                    for who in ("plain", mode) if index % 2 == 0 else (mode, "plain"):
                        await call(devices[who], condition, vec, index % rows, args.k)
                indices = list(range(len(queries)))
                rng.shuffle(indices)
                secure_first = [(j + rnd) % 2 == 0 for j in indices]
                rng.shuffle(secure_first)
                for pos, (qi, b_first) in enumerate(zip(indices, secure_first)):
                    rid = rng.randrange(rows)
                    gold = expected(condition, rid, truth[qi], rows)
                    for order, who in enumerate(
                        (mode, "plain") if b_first else ("plain", mode)
                    ):
                        start = perf_counter_ns()
                        result = await call(
                            devices[who], condition, queries[qi], rid, args.k
                        )
                        sdk_us = (perf_counter_ns() - start) / 1000
                        ids = validate_response(result, len(gold))
                        us = (perf_counter_ns() - start) / 1000
                        # Corpus-oracle comparison is independent evaluation work and is outside service latency.
                        if (
                            not (condition == "vector_fast" and who == mode)
                            and ids != gold
                        ):
                            raise RuntimeError(
                                f"incorrect {condition}/{who}, test query {qi}: {ids} vs {gold}"
                            )
                        samples.append(
                            {
                                "kind": condition,
                                "mode": "plain" if who == "plain" else label,
                                "round": rnd,
                                "position": pos,
                                "query": qi,
                                "field_id": rid,
                                "order": order,
                                "us": us,
                                "sdk_us": sdk_us,
                                "ids": ids,
                                "gold": gold,
                                "recall": len(set(ids) & set(gold)) / len(gold),
                                "exact_order": ids == gold,
                                "stats": result["stats"],
                            }
                        )
            print(
                f"Finished round {rnd + 1}/{args.rounds}", file=sys.stderr, flush=True
            )
        if not args.skip_io_control and "encrypted" in devices:
            dev = devices["encrypted"]
            for kind in ("exact", "vector_fast", "vector_refined"):
                if kind not in args.conditions:
                    continue
                field = kind == "exact"
                start = perf_counter_ns()
                scan = (
                    await dev.full_scan(field="ref", value="item-0")
                    if field
                    else await dev.full_scan(vector=queries[0], k=args.k)
                )
                scan_us = (perf_counter_ns() - start) / 1000
                if [rid for rid, _ in scan["records"]] != expected(
                    kind, 0, truth[0], rows
                ):
                    raise RuntimeError("invalid full-scan control")
                result = await call(dev, kind, queries[0], 0, args.k)
                control[kind] = {
                    "full_scan_us": scan_us,
                    "full_scan": scan["stats"],
                    "indexed": result["stats"],
                }
    return samples, first, control


def write_report(args, dataset, setup, peer, samples, first, control, beta):
    args.out.mkdir(parents=True, exist_ok=True)
    topology = {
        "loopback": "SDK, HPIC and HPIS on the same host, two loopback TLS hops",
        "direct": "SDK on the terminal, remote HPIC and HPIS connected over peer loopback TLS",
        "ssh-forward": "terminal-to-HPIC TLS over one shared SSH port-forward transport for all modes; HPIC-to-HPIS TLS on the peer loopback",
        "local-forward": "same-host test of external endpoint mode; no WAN measurement",
    }[args.transport]
    report = {
        "parameters": {
            "queries": args.queries,
            "rounds": args.rounds,
            "k": args.k,
            "beta": beta,
            "seed": args.seed,
            "conditions": args.conditions,
            "transport": args.transport,
        },
        "environment": {"terminal": machine(), "server": peer},
        "dataset": dataset,
        "timing": {
            "boundary": "SDK entry (including query normalization, token or sketch generation) through two service hops, authentication, decoding and terminal record-ID/body validation",
            "sdk_us": "SDK entry to decoded SDK response, before benchmark record checks",
            "topology": topology,
            "concurrency": 1,
            "connections": "persistent TLS 1.3",
            "design": "independent condition blocks; shuffled block and query order each round; balanced randomized AB/BA pairs, identical inputs",
            "warmup_pairs_per_condition_per_round": args.actual_warmups,
            "oracle": "exhaustive reference and recall comparison outside timed interval",
            "first_query": "first request observed in this run per endpoint; includes connection setup; index/OS cache state depends on prior service use",
        },
        "setup": setup,
        "first_vector_observed_us": first,
        "results": aggregate(samples, args.conditions, args.seed),
        "full_scan_control": {
            "description": "HPIS2 implementation of complete AID read and HPIC decryption; I/O is logical record/candidate bytes. Separate historical-source benchmark measures the original HPIS1 services.",
            "per_query": control,
        },
    }
    with gzip.open(args.out / "samples.jsonl.gz", "wt", encoding="utf-8") as f:
        for item in samples:
            f.write(json.dumps(item, separators=(",", ":")) + "\n")
    write_json(args.out / "summary.json", report)
    for kind, modes in report["results"].items():
        label = CONDITIONS[kind][1]
        enc = modes[label]
        print(
            f"{kind}: plain {modes['plain']['e2e_us']['mean']:.1f} us, {label} {enc['e2e_us']['mean']:.1f} us, "
            f"overhead {enc['overhead_pct']['mean']:.2f}%, recall {enc['recall']:.4f}",
            flush=True,
        )


def run(args):
    remote = args.command == "external"
    deployment = None
    if remote:
        deployment = json.loads((args.clients / "deployment.json").read_text())
        rows = deployment["dataset"]["rows"]
        if args.rows is not None and args.rows != rows:
            raise ValueError("--rows differs from the prepared corpus on the server")
        beta = deployment["beta"]
    else:
        rows, beta = args.rows or 20000, args.beta
    if (
        args.rounds < 1
        or not 1 <= args.k <= rows
        or args.queries < 1
        or args.warmups < 0
    ):
        raise ValueError("invalid query count, k, rounds or warmup count")
    if len(set(args.conditions)) != len(args.conditions):
        raise ValueError("duplicate benchmark condition")
    base, queries, warm, data = load_data(
        args.dataset, rows, args.queries, args.warmups, args.query_start
    )
    args.actual_warmups = len(warm)
    data["test_queries"] = len(queries)
    truth = oracle(base, queries, args.k)
    modes = [
        m
        for m in MODES
        if m == "plain" or any(CONDITIONS[c][0] == m for c in args.conditions)
    ]
    if remote:
        if data["dimensions"] != deployment["dataset"]["dimensions"]:
            raise ValueError("client and server vector dimensions differ")
        data["server_source"] = deployment["dataset"]
        urls = args.urls or [
            f"https://127.0.0.1:{args.base_port + i}" for i in range(len(MODES))
        ]
        endpoints = {m: (args.clients / m, urls[MODES.index(m)]) for m in modes}
        setup, peer = deployment["setup"], deployment["peer"]
        samples, first, control = asyncio.run(
            measure(args, endpoints, queries, warm, truth, rows)
        )
    else:
        with tempfile.TemporaryDirectory(prefix="hpis-bench-") as tmp:
            root = Path(tmp)
            with fleet(
                root, existing=False, dim=base.shape[1], beta=beta, modes=modes
            ) as endpoints:
                setup = asyncio.run(populate(endpoints, root, base))
                samples, first, control = asyncio.run(
                    measure(args, endpoints, queries, warm, truth, rows)
                )
        peer = machine()
    write_report(args, data, setup, peer, samples, first, control, beta)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser(
        "prepare",
        help="prepare durable test databases and portable synthetic client credentials",
    )
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--dataset", required=True, type=Path)
    p.add_argument("--rows", type=int, default=20000)
    p.add_argument("--beta", type=float, default=0.25)
    p = subs.add_parser(
        "serve", help="serve a prepared fleet on consecutive loopback HPIC ports"
    )
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--base-port", type=int, default=18541)
    for name in ("local", "external"):
        p = subs.add_parser(name)
        p.add_argument("--dataset", required=True, type=Path)
        p.add_argument("--rows", type=int)
        p.add_argument("--queries", type=int, default=256)
        p.add_argument(
            "--query-start",
            type=int,
            default=0,
            help="first held-out test row (zero based)",
        )
        p.add_argument("--rounds", type=int, default=3)
        p.add_argument("--k", type=int, default=10)
        p.add_argument("--warmups", type=int, default=8)
        p.add_argument("--seed", type=int, default=20261120)
        p.add_argument("--out", required=True, type=Path)
        p.add_argument(
            "--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS)
        )
        p.add_argument("--skip-io-control", action="store_true")
        if name == "external":
            p.add_argument("--clients", required=True, type=Path)
            p.add_argument("--base-port", type=int, default=18541)
            p.add_argument(
                "--urls",
                nargs=3,
                metavar=("PLAIN", "PPE", "TRUSTED"),
                help="explicit HPIC URLs",
            )
            p.add_argument(
                "--transport",
                required=True,
                choices=["ssh-forward", "direct", "local-forward"],
            )
        else:
            p.add_argument("--beta", type=float, default=0.25)
            p.set_defaults(transport="loopback")
    argv = sys.argv[1:]
    if argv and argv[0].startswith("--") and argv[0] != "--help":
        argv = ["local", *argv]
    args = parser.parse_args(argv)
    with threadpool_limits(limits=1, user_api="blas"):
        if args.command == "prepare":
            prepare(args)
        elif args.command == "serve":
            serve_prepared(args.root, args.base_port)
        else:
            run(args)


if __name__ == "__main__":
    main()
