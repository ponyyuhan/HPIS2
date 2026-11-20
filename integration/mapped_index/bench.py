"""Run the unfiltered exact path using independent HPIS/HPIC processes and real TLS."""

import argparse
import asyncio
import contextlib
import gzip
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys
import tempfile
import time
from time import perf_counter_ns

import h5py
import numpy as np
from threadpoolctl import threadpool_limits

from bench.cluster import free_port
from bench.run import content, machine, oracle, record
from hpis.provision import enroll, init
from hpis.codec import pack, unpack
from hpis.sdk import Device
from .protocol import MappedDevice


@contextlib.contextmanager
def running(root, mode, dim, beta=0.25, *, joint=False):
    root = Path(root)
    folder = init(root, mode, dim, beta)
    encrypted = None
    if joint:
        public_path = root / "public.msgpack"
        original = public_path.read_bytes()
        public = {**unpack(original), "mode": "encrypted"}
        public_path.write_bytes(pack(public))
        try:
            encrypted = enroll(root, "encrypted")
        finally:
            public_path.write_bytes(original)
    procs = []
    with (root / "mapped-servers.log").open("a+") as log:
        try:
            previous = None
            for role in ("hpis", "hpic"):
                port = free_port()
                cmd = [
                    sys.executable,
                    "-m",
                    "integration.mapped_index",
                    role,
                    str(root / role),
                    "--shared",
                    str(root / "shared"),
                    "--port",
                    str(port),
                ]
                if previous:
                    cmd += ["--store-url", previous]
                env = {
                    **os.environ,
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                    "VECLIB_MAXIMUM_THREADS": "1",
                }
                proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=env)
                procs.append(proc)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read()[-1800:])
                    try:
                        with socket.create_connection(("127.0.0.1", port), 0.1):
                            break
                    except OSError:
                        time.sleep(0.03)
                else:
                    raise RuntimeError("server start timed out")
                previous = f"https://127.0.0.1:{port}"
            yield (
                ((folder, previous), (encrypted, previous))
                if joint
                else (folder, previous)
            )
        finally:
            for proc in reversed(procs):
                proc.terminate()
            for proc in procs:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def summary(items):
    means = {}
    for mode in ("native_plain", "mapped_plain", "mapped_encrypted"):
        samples = [s for s in items if s["mode"] == mode]
        us = np.asarray([s["us"] for s in samples])
        means[mode] = {
            "n": len(us),
            "mean_us": float(us.mean()),
            "p50_us": float(np.median(us)),
            "p95_us": float(np.percentile(us, 95)),
            "opened_mean": float(np.mean([s["opened"] for s in samples])),
            "rank_mean_us": float(np.mean([s["rank_us"] for s in samples])),
            "auth_counts": {
                key: sum(int(s[key]) for s in samples)
                for key in (
                    "issuer_cache_hit",
                    "ticket_expiry_checks",
                    "holder_signature_checks",
                    "owner_signature_checks",
                    "nonce_checks",
                )
            },
        }
    reps = sorted({s["round"] for s in items})
    for baseline in ("native_plain", "mapped_plain"):
        p = np.asarray(
            [
                [s["us"] for s in items if s["round"] == r and s["mode"] == baseline]
                for r in reps
            ]
        )
        e = np.asarray(
            [
                [
                    s["us"]
                    for s in items
                    if s["round"] == r and s["mode"] == "mapped_encrypted"
                ]
                for r in reps
            ]
        )
        rng = np.random.default_rng(537)
        boot = []
        for _ in range(2000):
            ix = (
                rng.integers(
                    0, p.shape[1], size=(len(reps), (p.shape[1] + 15) // 16, 1)
                )
                + np.arange(16)
            ) % p.shape[1]
            ix = ix.reshape(len(reps), -1)[:, : p.shape[1]]
            boot.append(
                100
                * (
                    np.take_along_axis(e, ix, axis=1).mean()
                    / np.take_along_axis(p, ix, axis=1).mean()
                    - 1
                )
            )
        means["versus_" + baseline] = {
            "mean_overhead_pct": 100 * (e.mean() / p.mean() - 1),
            "round_overhead_pct": (
                100 * (e.mean(axis=1) / p.mean(axis=1) - 1)
            ).tolist(),
            "mean_ci95_block_pairs_16": np.percentile(boot, [2.5, 97.5]).tolist(),
        }
    return means


async def run(args, root, base, query, gold):
    timers = {}
    out = []
    first = {}
    with contextlib.ExitStack() as stack:
        if args.separate:
            ends = {
                k: stack.enter_context(running(root / k, k, base.shape[1]))
                for k in ("plain", "encrypted")
            }
        else:
            p, e = stack.enter_context(
                running(root / "joint", "plain", base.shape[1], joint=True)
            )
            ends = {"plain": p, "encrypted": e}
        async with contextlib.AsyncExitStack() as clients:
            mp = await clients.enter_async_context(
                MappedDevice(*ends["plain"], pipeline=not args.serial)
            )
            me = MappedDevice(*ends["encrypted"], pipeline=not args.serial)
            npd = Device(*ends["plain"])
            if args.separate:
                await clients.enter_async_context(me)
                await clients.enter_async_context(npd)
            else:
                # Two tenants on the same storage/PIC event loops and the same
                # terminal TLS session avoid host/core/connection confounding.
                me.session = npd.session = mp.session
            awaitables = {
                "mapped_plain": mp,
                "mapped_encrypted": me,
                "native_plain": npd,
            }
            for name, d in [("plain", mp), ("encrypted", me)]:
                start = perf_counter_ns()
                for i in range(0, len(base), 128):
                    await d.put(
                        record(j, base[j]) for j in range(i, min(i + 128, len(base)))
                    )
                timers[name] = {"ingest_s": (perf_counter_ns() - start) / 1e9}
            for name, d in awaitables.items():
                start = perf_counter_ns()
                r = await d.vector(query[0], args.k, refine=(name != "native_plain"))
                first[name] = {
                    "e2e_us": (perf_counter_ns() - start) / 1000,
                    "snapshot_bytes": r["stats"].get("preposition_bytes", 0),
                    "snapshot_build_us": r["stats"].get("preposition_us", 0),
                }
                assert [i for i, _ in r["records"]] == gold[0]
            for name in ("plain", "encrypted"):
                directory = root / (name if args.separate else "joint") / "shared"
                dev = mp if name == "plain" else me
                timers[name]["mapped_image_bytes"] = (
                    (directory / (dev.index_id.hex() + ".hpi")).stat().st_size
                )
            rng = random.Random(973)
            for rnd in range(args.rounds):
                order = list(range(len(query)))
                rng.shuffle(order)
                for qi in order[:8]:
                    for name, d in awaitables.items():
                        await d.vector(
                            query[qi], args.k, refine=(name != "native_plain")
                        )
                for pos, qi in enumerate(order):
                    arms = list(awaitables)
                    rng.shuffle(arms)
                    for idx, name in enumerate(arms):
                        t = perf_counter_ns()
                        r = await awaitables[name].vector(
                            query[qi], args.k, refine=(name != "native_plain")
                        )
                        rid = [i for i, _ in r["records"]]
                        assert all(v == content(i) for i, v in r["records"])
                        dt = (perf_counter_ns() - t) / 1000
                        assert rid == gold[qi], (qi, name, rid, gold[qi])
                        s = r["stats"]
                        assert s["body_rows"] == len(rid)
                        out.append(
                            dict(
                                round=rnd,
                                position=pos,
                                order=idx,
                                query=qi,
                                mode=name,
                                us=dt,
                                k=len(rid),
                                ids=rid,
                                opened=s.get("opened_vectors", 0),
                                rank_us=s.get("rank_us", s.get("store_us")),
                                store_us=s["store_us"],
                                body_bytes=s["body_bytes"],
                                gemv_us=s.get("gemv_us"),
                                select_us=s.get("select_us"),
                                exact_evaluations=s.get("exact_evaluations"),
                                data_alignment=s.get("data_alignment"),
                                query_alignment=s.get("query_alignment"),
                                **{
                                    key: s[key]
                                    for key in (
                                        "issuer_cache_hit",
                                        "ticket_expiry_checks",
                                        "holder_signature_checks",
                                        "owner_signature_checks",
                                        "nonce_checks",
                                    )
                                },
                            )
                        )
                print("finished round", rnd + 1, flush=True)
    return out, timers, first


def main():
    pp = argparse.ArgumentParser(description=__doc__)
    pp.add_argument(
        "--serial",
        action="store_true",
        help="run both mapped profiles with serial cloud hop",
    )
    pp.add_argument("--dataset", type=Path, required=True)
    pp.add_argument("--rows", type=int, default=20000)
    pp.add_argument("--queries", type=int, default=512)
    pp.add_argument("--rounds", type=int, default=4)
    pp.add_argument("--query-offset", type=int, default=0)
    pp.add_argument("--k", type=int, default=10)
    pp.add_argument(
        "--separate",
        action="store_true",
        help="assign plaintext and encrypted tenants different service processes",
    )
    pp.add_argument("--out", type=Path, required=True)
    args = pp.parse_args()
    with threadpool_limits(1), h5py.File(args.dataset) as f:
        base = f["train"][: args.rows]
        qs = f["test"][args.query_offset : args.query_offset + args.queries]
        gold = oracle(base, qs, args.k)
        with tempfile.TemporaryDirectory(prefix="hpis-mapped-run-") as tmp:
            out, setup, first = asyncio.run(run(args, Path(tmp), base, qs, gold))
        report = {
            "system": machine(),
            "rows": len(base),
            "dimensions": base.shape[1],
            "unique_queries": len(qs),
            "same_hpic_and_hpis_processes": not args.separate,
            "identical_public_ticket_cache_all_three_arms": True,
            "mapped_pipeline": not args.serial,
            "owner_bound_blinded_query": True,
            "native_fused": bool(os.environ.get("HPIS_MAPPED_NATIVE") or os.environ.get("HPIS_MAPPED_Q8")),
            "native_q8_opt_in": bool(os.environ.get("HPIS_MAPPED_Q8")),
            "query_offset": args.query_offset,
            "k": args.k,
            "rounds": args.rounds,
            "beta": 0.25,
            "top_k_order": 1.0,
            "setup": setup,
            "first_query_including_snapshot_us": first,
            "results": summary(out),
        }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    with gzip.open(args.out / "samples.jsonl.gz", "wt") as f:
        for row in out:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(json.dumps(report["results"], indent=2), flush=True)


if __name__ == "__main__":
    main()
