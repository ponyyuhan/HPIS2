"""HPIS 2.0 Linux acceptance benchmark: plaintext vs ciphertext retrieval, same stack.

Every request goes through the production SDK -> HPIC -> HPIS path: two loopback
TLS 1.3 hops (HPIC->HPIS mutually authenticated), anonymous holder ticket, owner
signature, sealed request envelope. All tenants live in one HPIS process, one HPIC
process and one SQLite file and share one persistent client TLS connection, so the two
arms of a pair differ only in how the data and the query target are represented.

Main scenarios (each: plaintext arm vs ciphertext arm, identical inputs):
  sse_exact    exact field (1 hit)        plaintext labels/bodies vs HMAC-SHA256 tags + AES-256-GCM bodies
  sse_keyword  keyword (10 hits)          same
  vec_tee      vector Top-K, TEE exact    plaintext unit vectors vs AES-GCM vector index opened in the trusted process
  vec_ppe      vector Top-K, PPE fast     plaintext unit vectors vs RAP sketches (h = xR + e), norm projection
  vec_ppe_rerank  PPE Top-K' at HPIS (K' = --rerank-k), HPIC decrypts the K' bodies and re-ranks
               them with the same FP32 kernel on the vector inside each body -> exact Top-K
  io           bytes HPIS reads/sends: HPIS 1.0-style whole-AID fetch vs 2.0 partial fetch

Appendix: three_arm (why the old report showed -1.14%: production FP64-recheck plaintext
vs the shared kernel vs PPE), refine (pure-crypto strict exact Top-K, refine=True),
mapped (HPIC-mapped exact profile; needs integration.mapped_index).

Timing: from SDK entry with the raw query (plaintext field value / raw vector) to the
SDK returning decoded plaintext records. Result checks and the exhaustive FP64
reference are outside the timed interval.
"""

import os

# Single-threaded BLAS everywhere; must precede the first numpy import (macOS Accelerate
# reads VECLIB_MAXIMUM_THREADS only at load time).
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse  # noqa: E402
import asyncio  # noqa: E402
import gc  # noqa: E402
import gzip  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from time import perf_counter_ns  # noqa: E402

import numpy as np  # noqa: E402
from threadpoolctl import threadpool_limits  # noqa: E402

from . import stats  # noqa: E402
from .cluster import (  # noqa: E402
    ROOT,
    beta_name,
    code_digest,
    machine,
    open_devices,
    pick_cpus,
    provision,
    services,
)
from .data import content, exact_topk, load, record  # noqa: E402
from .kernel import KERNEL_DESCRIPTION, SQLITE_PRAGMAS  # noqa: E402


@dataclass(frozen=True)
class Arm:
    key: str
    tenant: str
    kernel: str = "unified"  # server-side selector; only plain/TEE honour "production"
    refine: bool = False
    coarse_k: int = 0  # > 0: PPE Top-coarse_k at HPIS, exact Top-k re-rank at HPIC


@dataclass(frozen=True)
class Scenario:
    key: str
    name_zh: str
    kind: str  # field | word | vector
    arms: tuple  # arms[0] is the plaintext baseline
    section: str  # main | appendix


PLAIN = Arm("plaintext", "plain")


def scenarios(args):
    main = {
        "sse_exact": Scenario("sse_exact", "精确字段 SSE（命中 1 条）", "field", (PLAIN, Arm("sse_cipher", "ppe")), "main"),
        "sse_keyword": Scenario("sse_keyword", "关键词 SSE（命中 10 条）", "word", (PLAIN, Arm("sse_cipher", "ppe")), "main"),
        "vec_tee": Scenario("vec_tee", f"向量 Top-{args.k}·软硬协同精确（TEE）", "vector", (PLAIN, Arm("tee", "tee")), "main"),
        "vec_ppe": Scenario("vec_ppe", f"向量 Top-{args.k}·纯密码学 PPE（快速）", "vector", (PLAIN, Arm("ppe", "ppe")), "main"),
        "vec_ppe_rerank": Scenario(
            "vec_ppe_rerank",
            f"向量 Top-{args.k}·纯密码学 PPE 粗排 {args.rerank_k} + HPIC 精排",
            "vector",
            (PLAIN, Arm("ppe_rerank", "ppe", coarse_k=args.rerank_k)),
            "main",
        ),
    }
    chosen = [main[k] for k in args.scenarios]
    for b in args.extra_betas:
        name = beta_name(b)
        chosen.append(
            Scenario(f"vec_{name}", f"向量 Top-{args.k}·PPE（β={b:g}）", "vector", (PLAIN, Arm(name, name)), "main")
        )
        if args.rerank_extra_k:
            chosen.append(
                Scenario(
                    f"vec_{name}_rerank",
                    f"向量 Top-{args.k}·PPE（β={b:g}）粗排 {args.rerank_extra_k} + HPIC 精排",
                    "vector",
                    (PLAIN, Arm(f"{name}_rerank", name, coarse_k=args.rerank_extra_k)),
                    "appendix",
                )
            )
    if "three_arm" in args.appendix:
        # The FP64-recheck arm reads its own identical plaintext tenant: an arm that shares
        # a matrix with another arm would inherit a warm cache from it (carry-over bias).
        chosen.append(
            Scenario(
                "three_arm",
                "旧口径对照：明文FP64复核 / 明文同核 / PPE",
                "vector",
                (Arm("plaintext_fp64_recheck", "plain_b", kernel="production"), PLAIN, Arm("ppe", "ppe")),
                "appendix",
            )
        )
    if "refine" in args.appendix:
        chosen.append(
            Scenario(
                "refine_strict",
                f"纯密码学严格精排 Top-{args.k}（refine）",
                "vector",
                (PLAIN, Arm("ppe_refine", "ppe", refine=True)),
                "appendix",
            )
        )
    return chosen


MAPPED = Scenario(
    "mapped_exact",
    "HPIC 预置索引严格 Top-K（mapped）",
    "vector",
    (PLAIN, Arm("mapped_exact", "mapped_ppe", refine=True)),
    "appendix",
)


def balanced_orders(m, n, rng):
    """Every permutation of m arms once per consecutive group of m! positions."""
    perms = list(itertools.permutations(range(m)))
    out = []
    while len(out) < n:
        group = perms[:]
        rng.shuffle(group)
        out.extend(group)
    return out[:n]


class Context:
    def __init__(self, args, ds, truth, devices, svc):
        self.args, self.ds, self.truth, self.devices, self.svc = args, ds, truth, devices, svc
        self.rows = len(ds.base)

    async def issue(self, sc, arm, qvec, rid):
        dev = self.devices[arm.tenant]
        dev.kernel = arm.kernel
        k = self.args.k
        dev.rerank = (k, arm.coarse_k) if arm.coarse_k else None
        t0 = perf_counter_ns()
        if sc.kind == "field":
            res = await dev.terms([["field", "ref", f"item-{rid}"]])
        elif sc.kind == "word":
            res = await dev.terms([["word", f"g{rid // 10}"]])
        else:
            res = await dev.vector(qvec, k, refine=arm.refine)
        return res, (perf_counter_ns() - t0) / 1000

    def expected(self, sc, rid):
        if sc.kind == "field":
            return [rid]
        g = rid // 10
        return list(range(10 * g, min(self.rows, 10 * g + 10)))

    def check(self, sc, arm, res, rid):
        """Outside timing: bodies must be byte-exact, SSE results exact, K distinct ids."""
        records = res["records"]
        ids = [r for r, _ in records]
        size = self.args.body_bytes
        for r, text in records:
            if text != content(r, size):
                raise RuntimeError(f"{sc.key}/{arm.key}: wrong body for record {r}")
        read = min(arm.coarse_k, self.rows) if arm.coarse_k else len(ids)
        if res["stats"]["body_rows"] != read:
            raise RuntimeError(f"{sc.key}/{arm.key}: body rows read {res['stats']['body_rows']} != {read}")
        if sc.kind in ("field", "word"):
            if ids != self.expected(sc, rid):
                raise RuntimeError(f"{sc.key}/{arm.key}: wrong SSE result for {rid}: {ids}")
        elif len(ids) != min(self.args.k, self.rows) or len(set(ids)) != len(ids):
            raise RuntimeError(f"{sc.key}/{arm.key}: malformed Top-K {ids}")
        return ids


STAT_KEYS = {
    "store_us": "store_us",
    "sql_us": "sql_us",
    "open_and_filter_us": "open_us",
    "body_rows": "body_rows",
    "body_bytes": "body_bytes",
    "candidate_rows": "cand_rows",
    "candidate_bytes": "cand_bytes",
    "candidate_wire_data_bytes": "cand_wire_bytes",
    "refined_rows": "refined_rows",
}


async def block(ctx, sc, rnd, rng, samples):
    # The harness's own growing sample list must not cause cyclic-GC pauses inside timed
    # requests: collect + freeze between blocks, no automatic GC within a block (client
    # process only; HPIC/HPIS processes keep their normal GC).
    gc.collect()
    gc.freeze()
    gc.disable()
    try:
        await _block(ctx, sc, rnd, rng, samples)
    finally:
        gc.enable()


async def _block(ctx, sc, rnd, rng, samples):
    args, ds = ctx.args, ctx.ds
    arms = sc.arms
    for w in range(args.warmup):
        qvec = ds.warm[w % len(ds.warm)] if len(ds.warm) else ds.queries[w % len(ds.queries)]
        rid = rng.randrange(ctx.rows)
        order = arms[w % len(arms):] + arms[: w % len(arms)]
        for arm in order if w % 2 == 0 else order[::-1]:
            res, _ = await ctx.issue(sc, arm, qvec, rid)
            ctx.check(sc, arm, res, rid)
    # Positions = every query once, shuffled. They are grouped into runs of R consecutive
    # positions; within a run each arm issues all R queries back to back, arm order per run
    # balanced (AB/BA, or all 6 orders for 3 arms). R = 1 is request-level interleaving;
    # R = 16 keeps each arm's caches warm (steady state of a single active tenant).
    n = len(ds.queries)
    positions = list(range(n))
    rng.shuffle(positions)
    rids = [rng.randrange(ctx.rows) for _ in range(n)]
    size = max(1, args.run_length)
    runs = [range(i, min(i + size, n)) for i in range(0, n, size)]
    for run, perm in zip(runs, balanced_orders(len(arms), len(runs), rng)):
        got = []
        for slot, ai in enumerate(perm):
            arm = arms[ai]
            for pos in run:
                res, us = await ctx.issue(sc, arm, ds.queries[positions[pos]], rids[pos])
                got.append((pos, slot, arm, res, us))
        by_pos = {}
        for pos, slot, arm, res, us in got:
            qi, rid = positions[pos], rids[pos]
            ids = ctx.check(sc, arm, res, rid)
            st = res["stats"]
            item = {
                "s": sc.key,
                "arm": arm.key,
                "r": rnd,
                "pos": pos,
                "slot": slot,
                "q": qi,
                "rid": rid,
                "us": round(us, 3),
                "st": {short: st[key] for key, short in STAT_KEYS.items() if key in st},
            }
            if sc.kind == "vector":
                truth = ctx.truth[qi]
                item.update(ids=ids, recall=len(set(ids) & set(truth)) / len(truth), exact=ids == truth)
            samples.append(item)
            by_pos.setdefault(pos, []).append(item)
        if sc.kind == "vector":
            # Ordered equality / overlap with the baseline arm's answer for the same query.
            for items in by_pos.values():
                base_ids = next(s["ids"] for s in items if s["arm"] == arms[0].key)
                for s in items:
                    s["same_as_plaintext"] = s["ids"] == base_ids
                    s["recall_vs_plaintext"] = len(set(s["ids"]) & set(base_ids)) / len(base_ids)


async def cold_queries(ctx, tenants):
    """First request per tenant after ingest (index load / TEE index decryption)."""
    out = {}
    q = ctx.ds.queries[0]
    for name in tenants:
        dev = ctx.devices[name]
        dev.kernel, dev.rerank = "unified", None
        t0 = perf_counter_ns()
        await dev.vector(q, ctx.args.k)
        out[name] = {"first_vector_us": (perf_counter_ns() - t0) / 1000}
    return out


async def ingest(devices, names, base, size, chunk=128):
    spent = {n: 0.0 for n in names}
    for c, start in enumerate(range(0, len(base), chunk)):
        rows = [record(i, base[i], size) for i in range(start, min(start + chunk, len(base)))]
        rot = c % len(names)
        for name in names[rot:] + names[:rot]:
            t0 = time.perf_counter()
            await devices[name].put(rows)
            spent[name] += time.perf_counter() - t0
        if c % 100 == 0:
            print(f"  ingest {start + len(rows)}/{len(base)}", file=sys.stderr, flush=True)
    return spent


async def io_block(ctx, n_items, rng):
    """HPIS 1.0-style whole-AID fetch (op 'scan', HPIC decrypts all) vs 2.0 partial fetch,
    on the ciphertext (PPE) tenant, plain fast PPE queries (no re-rank)."""
    dev = ctx.devices["ppe"]
    dev.kernel, dev.rerank = "unified", None
    k = ctx.args.k
    out = []

    async def measured(style, kind, call, rid, qi):
        # Logical body bytes as counted by HPIS. (Kernel /proc/<pid>/io counters stay 0
        # here: SQLite reads via mmap and asyncio sockets use send(2), not read/write.)
        t0 = perf_counter_ns()
        res = await call()
        us = (perf_counter_ns() - t0) / 1000
        ids = [r for r, _ in res["records"]]
        st = res["stats"]
        out.append({
            "style": style,
            "kind": kind,
            "rid": rid,
            "q": qi,
            "us": round(us, 1),
            "body_rows": st["body_rows"],
            "body_bytes": st["body_bytes"],
            "ids": ids,
        })
        return ids

    for j in range(n_items):
        qi = j % len(ctx.ds.queries)
        rid = rng.randrange(ctx.rows)
        q = ctx.ds.queries[qi]
        g = rid // 10
        ids = await measured("2.0", "field", lambda: dev.terms([["field", "ref", f"item-{rid}"]]), rid, qi)
        assert ids == [rid]
        ids = await measured("2.0", "word", lambda: dev.terms([["word", f"g{g}"]]), rid, qi)
        assert ids == list(range(10 * g, min(ctx.rows, 10 * g + 10)))
        await measured("2.0", "vector", lambda: dev.vector(q, k), rid, qi)
        ids = await measured("1.0", "field", lambda: dev.full_scan(field="ref", value=f"item-{rid}"), rid, qi)
        assert ids == [rid]
        ids = await measured("1.0", "vector", lambda: dev.full_scan(vector=q, k=k), rid, qi)
        out[-1]["exact"] = ids == ctx.truth[qi]
        print(f"  io {j + 1}/{n_items}", file=sys.stderr, flush=True)
    return out


def summarize_io(rows, dim, n):
    if not rows:
        return None
    res = {}
    for style in ("1.0", "2.0"):
        for kind in ("field", "word", "vector"):
            xs = [r for r in rows if r["style"] == style and r["kind"] == kind]
            if not xs:
                continue
            item = {
                "requests": len(xs),
                "mean_ms": float(np.mean([r["us"] for r in xs]) / 1000),
                "body_rows": float(np.mean([r["body_rows"] for r in xs])),
                "body_bytes": float(np.mean([r["body_bytes"] for r in xs])),
            }
            if style == "1.0" and kind == "vector":
                item["exact_top_k"] = float(np.mean([r.get("exact", False) for r in xs]))
            res[f"{style}_{kind}"] = item
    full = res["1.0_field"]
    for kind in ("field", "word", "vector"):
        part = res.get(f"2.0_{kind}")
        if part:
            part["reduction_vs_1.0_body_bytes"] = full["body_bytes"] / max(part["body_bytes"], 1)
    res["note"] = (
        "1.0-style = op 'scan': HPIS reads every record of the AID and ships it to HPIC, "
        "which decrypts all and filters (the keyword case reads the same whole AID). "
        "2.0 = HPIS selects inside the AID and ships only the hits. The vector query still "
        f"scans the in-memory index each time ({n} x {dim} x 4 B = {n * dim * 4} B of RAM); "
        "what 2.0 removes is the per-query body read, transfer and decryption."
    )
    res["in_memory_vector_index_bytes"] = n * dim * 4
    return res


def arm_summary(xs, kind):
    us = [s["us"] for s in xs]
    out = {"latency_us": stats.describe(us)}

    def mean(key):
        vals = [s["st"].get(key) for s in xs if key in s["st"]]
        return float(np.mean(vals)) if vals else None

    store, sql, opened = mean("store_us"), mean("sql_us"), mean("open_us")
    out["phases_mean_us"] = {
        "hpis_search_total": store,
        "hpis_sqlite_body_read": sql,
        "hpic_decrypt_unpack": opened,
        "client_and_transport_rest": float(np.mean(us)) - (store or 0) - (opened or 0),
    }
    out["body_rows_per_query"] = mean("body_rows")
    out["body_bytes_per_query"] = mean("body_bytes")
    for key in ("cand_rows", "cand_bytes", "cand_wire_bytes", "refined_rows"):
        v = mean(key)
        if v:
            out[key + "_per_query"] = v
    if kind == "vector":
        out["recall_at_k_vs_fp64_truth"] = float(np.mean([s["recall"] for s in xs]))
        out["ordered_equal_fp64_truth"] = float(np.mean([s["exact"] for s in xs]))
        out["ordered_equal_plaintext_arm"] = float(np.mean([s["same_as_plaintext"] for s in xs]))
        out["recall_at_k_vs_plaintext_arm"] = float(np.mean([s["recall_vs_plaintext"] for s in xs]))
    return out


def compare(xs, base, other, seed):
    by = {}
    for s in xs:
        by.setdefault((s["r"], s["pos"]), {})[s["arm"]] = s["us"]
    rounds = []
    for r in sorted({s["r"] for s in xs}):
        keys = sorted(p for (rr, p) in by if rr == r)
        rounds.append(np.array([[by[(r, p)][base], by[(r, p)][other]] for p in keys]))
    return stats.paired(rounds, seed)


def correctness_text(sc, arms):
    cipher = arms[sc.arms[1].key]
    if sc.kind in ("field", "word"):
        n = cipher["latency_us"]["n"]
        return f"命中结果与明文一致 100%（{n}/{n}）"
    if sc.key == "vec_tee":
        return f"Top-10 与明文逐条同序一致 {100 * cipher['ordered_equal_plaintext_arm']:.2f}%"
    if sc.key.endswith("_rerank"):
        return (
            f"Top-10 与明文逐条同序 {100 * cipher['ordered_equal_plaintext_arm']:.2f}%，"
            f"Recall@10 {100 * cipher['recall_at_k_vs_fp64_truth']:.2f}%"
        )
    if sc.key in ("refine_strict", "mapped_exact"):
        return f"Top-10 与 FP64 穷举真值同序 {100 * cipher['ordered_equal_fp64_truth']:.2f}%"
    return f"Recall@10 {100 * cipher['recall_at_k_vs_fp64_truth']:.2f}%"


def summarize(sc, samples, seed):
    xs = [s for s in samples if s["s"] == sc.key]
    arms = {a.key: arm_summary([s for s in xs if s["arm"] == a.key], sc.kind) for a in sc.arms}
    if len(sc.arms) == 2:
        pairs = [(sc.arms[0], sc.arms[1])]
    else:
        a, b, c = sc.arms
        pairs = [(a, c), (b, c), (a, b)]
    comps = {f"{o.key}_vs_{bl.key}": compare(xs, bl.key, o.key, seed) for bl, o in pairs}
    out = {"name_zh": sc.name_zh, "section": sc.section, "kind": sc.kind, "arms": arms, "comparisons": comps}
    out["arm_definitions"] = {
        a.key: {"tenant": a.tenant, "kernel": a.kernel, "refine": a.refine} for a in sc.arms
    }
    if len(sc.arms) == 2:
        head = comps[f"{sc.arms[1].key}_vs_{sc.arms[0].key}"]
        out["headline"] = {
            "plaintext_mean_ms": head["baseline_mean_us"] / 1000,
            "cipher_mean_ms": head["other_mean_us"] / 1000,
            "overhead_pct": head["overhead_pct"]["mean"],
            "ci95": head["ci95_pct"]["mean"],
            "p95_overhead_pct": head["overhead_pct"]["p95"],
            "p95_ci95": head["ci95_pct"]["p95"],
            "delta_us": head["delta_mean_us"],
            "pairs": head["pairs"],
            "correctness": correctness_text(sc, arms),
            "body_bytes": {
                "plaintext": arms[sc.arms[0].key]["body_bytes_per_query"],
                "cipher": arms[sc.arms[1].key]["body_bytes_per_query"],
            },
            "below_10pct": head["overhead_pct"]["mean"] < 10,
            "ci_upper_below_10pct": head["ci95_pct"]["mean"][1] < 10,
        }
    return out


def tenant_storage(db_path, aids):
    """Logical stored bytes per tenant (outside timing)."""
    out = {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        for name, aid in aids.items():
            body = db.execute("SELECT count(*), coalesce(sum(length(body)),0) FROM docs WHERE aid=?", (aid,)).fetchone()
            vec = db.execute(
                "SELECT coalesce(sum(length(sketch)),0), coalesce(sum(length(original)),0) FROM vectors WHERE aid=?",
                (aid,),
            ).fetchone()
            post = db.execute("SELECT count(*), coalesce(sum(length(tag)),0) FROM postings WHERE aid=?", (aid,)).fetchone()
            out[name] = {
                "records": body[0],
                "body_bytes": body[1],
                "vector_index_bytes": vec[0],
                "vector_original_bytes": vec[1],
                "postings": post[0],
                "posting_tag_bytes": post[1],
            }
    return out


async def run_all(args, ds, truth, folders, tenants, svc):
    devices, http = open_devices(folders, svc["url"])
    try:
        ctx = Context(args, ds, truth, devices, svc)
        names = list(folders)
        print(f"ingest {len(ds.base)} rows into {len(names)} tenants", file=sys.stderr, flush=True)
        ingest_s = await ingest(devices, names, ds.base, args.body_bytes)
        cold = await cold_queries(ctx, names)
        gc.collect()
        gc.freeze()
        rng = random.Random(args.seed)
        samples = []
        plan = scenarios(args)
        for rnd in range(args.rounds):
            order = plan[:]
            rng.shuffle(order)
            t0 = time.perf_counter()
            for sc in order:
                await block(ctx, sc, rnd, rng, samples)
            print(f"round {rnd + 1}/{args.rounds} done in {time.perf_counter() - t0:.1f}s", file=sys.stderr, flush=True)
        io_rows = await io_block(ctx, args.io_queries, rng) if args.io_queries else []
        return samples, io_rows, {"ingest_s": ingest_s, "cold": cold}
    finally:
        await http.close()


async def mapped_session(args, ds, truth, folders, svc, samples):
    """Appendix: HPIC-mapped exact profile (integration.mapped_index) vs the same plaintext arm."""
    from integration.mapped_index.protocol import MappedDevice

    devices, http = open_devices({"plain": folders["plain"]}, svc["url"])
    mapped = MappedDevice(folders["ppe"], svc["url"], pipeline=True)
    mapped.session = http
    devices["mapped_ppe"] = mapped
    try:
        ctx = Context(args, ds, truth, devices, svc)
        print(f"mapped appendix: ingest {len(ds.base)} rows", file=sys.stderr, flush=True)
        ingest_s = await ingest(devices, ["plain", "mapped_ppe"], ds.base, args.body_bytes)
        t0 = perf_counter_ns()
        first = await mapped.vector(ds.queries[0], args.k, refine=True)
        cold_us = (perf_counter_ns() - t0) / 1000
        await devices["plain"].vector(ds.queries[0], args.k)
        gc.collect()
        rng = random.Random(args.seed + 1)
        for rnd in range(args.rounds):
            await block(ctx, MAPPED, rnd, rng, samples)
            print(f"mapped round {rnd + 1}/{args.rounds}", file=sys.stderr, flush=True)
        return {
            "ingest_s": ingest_s,
            "first_query_us_including_index_publication": cold_us,
            "index_publication_bytes": first["stats"].get("preposition_bytes"),
            "index_publication_us": first["stats"].get("preposition_us"),
            "native_ranker": os.environ.get("HPIS_MAPPED_NATIVE") is not None,
            "pipeline": True,
        }
    finally:
        await http.close()


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m acceptance.run",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dataset", type=Path, required=True, help="ann-benchmarks .hdf5, or raw float32 .f32/.npy")
    p.add_argument("--rows", type=int, default=20000, help="records inserted per tenant")
    p.add_argument("--queries", type=int, default=512, help="distinct held-out query vectors")
    p.add_argument("--query-start", type=int, default=0, help="first test row used as a query")
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--warmup", type=int, default=32, help="unrecorded pairs before every block")
    p.add_argument(
        "--run-length", type=int, default=1,
        help="consecutive queries per arm before switching (1 = request-level AB/BA; 16 = warm-cache variant)",
    )
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--beta", type=float, default=0.25, help="RAP perturbation of the PPE tenant")
    p.add_argument("--extra-betas", type=float, nargs="*", default=[], help="extra PPE tenants, e.g. 0.125")
    p.add_argument(
        "--scenarios", nargs="+", default=["sse_exact", "sse_keyword", "vec_tee", "vec_ppe", "vec_ppe_rerank"],
        choices=["sse_exact", "sse_keyword", "vec_tee", "vec_ppe", "vec_ppe_rerank"],
    )
    p.add_argument("--rerank-k", type=int, default=40, help="vec_ppe_rerank: PPE candidates HPIS returns (K')")
    p.add_argument(
        "--rerank-extra-k", type=int, default=20,
        help="K' for an appendix re-rank scenario on each --extra-betas tenant (0 = none)",
    )
    p.add_argument(
        "--appendix", nargs="*", default=["three_arm", "refine"], choices=["three_arm", "refine", "mapped"],
        help="mapped needs integration.mapped_index; set HPIS_MAPPED_NATIVE=<.so> for its C ranker",
    )
    p.add_argument("--io-queries", type=int, default=8, help="whole-AID fetch comparisons (0 = skip)")
    p.add_argument(
        "--body-bytes", type=int, default=128,
        help="memo text per record (UTF-8 bytes); short bodies keep shared costs small (stricter ratio)",
    )
    p.add_argument("--dim", type=int, help="vector dimension of a raw .f32 dataset")
    p.add_argument("--query-noise", type=float, default=0.0, help="raw datasets: noise norm added to held-out queries")
    p.add_argument("--seed", type=int, default=20261120)
    p.add_argument(
        "--pin", choices=["auto", "off"], default="off",
        help="pin client/HPIC/HPIS to distinct cores (Linux); off measured less noisy on shared hosts",
    )
    p.add_argument("--platform-label", default=None, help="short label for reports, e.g. 'Linux x86_64 (vast.ai)'")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    if args.rounds < 1 or args.queries < 2 or args.k < 1 or args.rows < max(20, args.k):
        p.error("need rounds >= 1, queries >= 2, rows >= max(20, k)")

    started = datetime.now(timezone.utc)
    wall0 = time.perf_counter()
    args.out.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(limits=1, user_api="blas"):
        ds = load(
            args.dataset, args.rows, args.queries, args.warmup,
            query_start=args.query_start, dim=args.dim, query_noise=args.query_noise, seed=args.seed,
        )
        t0 = time.perf_counter()
        truth = exact_topk(ds.base, ds.queries, args.k)
        oracle_s = time.perf_counter() - t0
        cpus = pick_cpus(3) if args.pin == "auto" else None
        if cpus and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, {cpus[0]})
        tmp = Path(tempfile.mkdtemp(prefix="hpis-acceptance-"))
        try:
            folders, tenants = provision(
                tmp, ds.base.shape[1], args.beta, args.extra_betas, second_plain="three_arm" in args.appendix
            )
            with services(tmp, cpus) as svc:
                samples, io_rows, setup = asyncio.run(run_all(args, ds, truth, folders, tenants, svc))
            from hpis.provision import read_config

            aids = {name: read_config(folder)["aid"] for name, folder in folders.items()}
            setup["stored_bytes"] = tenant_storage(tmp / "hpis" / "store.sqlite", aids)
            shutil.copy(tmp / "services.log", args.out / "services.log")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if "mapped" in args.appendix:
            tmp = Path(tempfile.mkdtemp(prefix="hpis-acceptance-mapped-"))
            try:
                folders, _ = provision(tmp, ds.base.shape[1], args.beta, with_tee=False)
                with services(tmp, cpus, mapped_shared=tmp / "shared") as svc:
                    setup["mapped"] = asyncio.run(mapped_session(args, ds, truth, folders, svc, samples))
                shutil.copy(tmp / "services.log", args.out / "services_mapped.log")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        env = machine()

    plan = scenarios(args) + ([MAPPED] if "mapped" in args.appendix else [])
    results = {sc.key: summarize(sc, samples, args.seed) for sc in plan}
    report = {
        "schema": "hpis2-acceptance-run/1",
        "started_utc": started.isoformat(timespec="seconds"),
        "wall_s": round(time.perf_counter() - wall0, 1),
        "command": "python -m acceptance.run " + " ".join(sys.argv[1:] if argv is None else argv),
        "platform_label": args.platform_label,
        "requirement": "ciphertext end-to-end latency < 1.10 x plaintext (SR 2.2.4)",
        "parameters": {
            "rows": args.rows, "queries": args.queries, "query_start": args.query_start,
            "rounds": args.rounds, "warmup_pairs_per_block": args.warmup, "k": args.k,
            "run_length": args.run_length, "rerank_k": args.rerank_k, "rerank_extra_k": args.rerank_extra_k,
            "beta": args.beta, "extra_betas": args.extra_betas, "body_bytes": args.body_bytes,
            "seed": args.seed, "io_queries": args.io_queries,
            "scenarios": [sc.key for sc in plan],
        },
        "dataset": ds.info,
        "environment": env,
        "pinning": {"client": cpus[0], "hpic": cpus[1], "hpis": cpus[2]} if cpus else None,
        "code": {"hpis": code_digest([ROOT / "hpis"]), "acceptance": code_digest([ROOT / "acceptance"])},
        "method": {
            "topology": "SDK client, HPIC and HPIS as three processes on one host; two loopback TLS 1.3 hops "
            "(HPIC->HPIS mutual TLS); all tenants in one HPIS process / one SQLite file; one shared "
            "persistent client connection; concurrency 1",
            "timing": "SDK entry with the raw query -> SDK returns decoded plaintext records; "
            "result checks and FP64 reference outside the timer",
            "design": "per round: scenario blocks in random order; per block: warm-up, then every "
            "query once in random order, grouped into runs of "
            f"{args.run_length} quer{'y' if args.run_length == 1 else 'ies'}; within a run each arm issues "
            "the run's queries back to back and every arm order (AB/BA, or all 6 for three arms) occurs "
            "once per consecutive group of runs; no two arms share a tenant; client cyclic GC frozen "
            "between blocks and paused within a block (servers unchanged)",
            "vector_kernel": KERNEL_DESCRIPTION,
            "ppe_rerank": f"HPIS: production PPE fast search for Top-{args.rerank_k} (bodies stay AES-GCM); "
            "HPIC: decrypts them with the DEK it already holds, re-ranks with the same FP32 kernel on the "
            "normalized vector stored inside each body (holder-signed grant field scan={kind: rerank, q, k}, "
            "applied by make_hpic_app's post-decryption filter hook); returns Top-k",
            "blas_threads": 1,
            "storage": "one SQLite file for all tenants, memory-mapped with a 256 MiB page cache "
            f"({'; '.join(SQLITE_PRAGMAS)}), identical for both arms",
            "record": f"field ref=item-<id>, keyword g<id//10>, vector, ~{args.body_bytes} B UTF-8 memo text; "
            "the SDK also stores the raw vector inside the body",
            "oracle": f"exhaustive FP64 cosine on the stored unit FP32 vectors, ties by id ({oracle_s:.1f}s, untimed)",
        },
        "setup": setup,
        "scenarios": results,
        "io": summarize_io(io_rows, ds.base.shape[1], len(ds.base)),
    }
    (args.out / "summary.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    with gzip.open(args.out / "samples.jsonl.gz", "wt", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, separators=(",", ":")) + "\n")
    if io_rows:
        with gzip.open(args.out / "io_samples.jsonl.gz", "wt", encoding="utf-8") as f:
            for s in io_rows:
                f.write(json.dumps(s, separators=(",", ":")) + "\n")
    print(f"\n{ds.info['name']}  rows={len(ds.base)} dims={ds.base.shape[1]}  {env.get('cpu')}")
    for key, r in results.items():
        if "headline" in r:
            h = r["headline"]
            print(
                f"{key:<14} plain {h['plaintext_mean_ms']:.3f} ms  cipher {h['cipher_mean_ms']:.3f} ms  "
                f"{h['overhead_pct']:+.2f}% [{h['ci95'][0]:+.2f}, {h['ci95'][1]:+.2f}]  "
                f"p95 {h['p95_overhead_pct']:+.2f}%  {h['correctness']}"
            )
        else:
            for name, c in r["comparisons"].items():
                print(f"{key:<14} {name}: {c['overhead_pct']['mean']:+.2f}% [{c['ci95_pct']['mean'][0]:+.2f}, {c['ci95_pct']['mean'][1]:+.2f}]")
    if report["io"]:
        io = report["io"]
        print(f"io: 1.0 whole-AID {io['1.0_field']['body_bytes']:.0f} B/query vs 2.0 field {io['2.0_field']['body_bytes']:.0f} B, vector {io['2.0_vector']['body_bytes']:.0f} B")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
