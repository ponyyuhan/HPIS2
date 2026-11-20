"""Build acceptance/results/summary.json (read by the demo dashboard) from per-run summaries.

python -m acceptance.rollup [--main DIR] [--scale DIR] [--business DIR] [--warm DIR]
                            [--replicate DIR] [--secondary DIR] [--earlier DIR ...] [--out FILE]
Run directories are acceptance/results/<run-name>/ written by acceptance.run.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

MAIN_KEYS = ("sse_exact", "sse_keyword", "vec_tee", "vec_ppe", "vec_ppe_rerank")
TAGS = {"sse_exact": "SSE", "sse_keyword": "SSE", "vec_tee": "TEE", "vec_ppe": "PPE", "vec_ppe_rerank": "PPE+HPIC"}
DESC = {
    "sse_exact": "按唯一字段值查 1 条。明文：明文字段值查明文倒排、取明文正文；密文：HMAC-SHA256 令牌查密文倒排、取 AES-256-GCM 正文，HPIC 解密。",
    "sse_keyword": "按关键词查 10 条，其余同上。",
    "vec_tee": "向量索引以 AES-256-GCM 存储，只在 HPIS 可信计算区内解开，用与明文完全相同的排序内核，结果与明文逐条同序。",
    "vec_ppe": "HPIS 只持有 RAP 扰动表示 h = xR + e（β=0.25），不依赖可信硬件；同一排序内核，近似检索。",
    "vec_ppe_rerank": "HPIS 用 PPE 快速检索取 40 条密文正文；HPIC 用已持有的 DEK 解密，按正文里的原向量以同一内核精排出 Top-10。不依赖可信硬件。",
}
NAMES = {
    "sse_exact": "精确字段 SSE（命中 1 条）",
    "sse_keyword": "关键词 SSE（命中 10 条）",
    "vec_tee": "向量 Top-10 · 软硬协同精确（TEE）",
    "vec_ppe": "向量 Top-10 · 纯密码学 PPE",
    "vec_ppe_rerank": "向量 Top-10 · 纯密码学 PPE 粗排 40 + HPIC 精排",
}


def load(path):
    if not path:
        return None
    f = Path(path) / "summary.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else None


def platform_label(run):
    env = run["environment"]
    label = run.get("platform_label") or env.get("os", "")
    return f"{label} · {env.get('cpu')}"


def r2(x, n=2):
    return None if x is None else round(float(x), n)


def head(run, key):
    sc = run["scenarios"].get(key)
    if not sc or "headline" not in sc:
        return None
    h = sc["headline"]
    return {
        "plaintext_mean_ms": r2(h["plaintext_mean_ms"], 4),
        "cipher_mean_ms": r2(h["cipher_mean_ms"], 4),
        "overhead_pct": r2(h["overhead_pct"]),
        "ci95": [r2(x) for x in h["ci95"]],
        "p95_overhead_pct": r2(h["p95_overhead_pct"]),
        "delta_us": r2(h["delta_us"], 1),
        "pairs": h["pairs"],
        "correctness": h["correctness"],
        "body_bytes": {k: r2(v, 0) for k, v in h["body_bytes"].items()},
        "pass": bool(h["overhead_pct"] < 10),
        "ci_upper_below_10pct": bool(h["ci95"][1] < 10),
    }


def dataset_name(run):
    ds = run["dataset"]
    if ds.get("format") == "raw float32 matrix" and "business" in ds["name"]:
        return "真实业务邮件向量（bge-small-zh-v1.5）"
    return ds["name"]


def short(run, key):
    h = head(run, key)
    if not h:
        return None
    return {
        "rows": run["dataset"]["rows"],
        "dims": run["dataset"]["dims"],
        "dataset": dataset_name(run),
        "run_length": run["parameters"].get("run_length", 1),
        "platform": platform_label(run),
        "queries": run["parameters"]["queries"],
        "rounds": run["parameters"]["rounds"],
        **h,
    }


def comparison(run, key, name):
    sc = run["scenarios"].get(key)
    if not sc:
        return None
    c = sc["comparisons"][name]
    return {
        "baseline_mean_ms": r2(c["baseline_mean_us"] / 1000, 4),
        "other_mean_ms": r2(c["other_mean_us"] / 1000, 4),
        "overhead_pct": r2(c["overhead_pct"]["mean"]),
        "ci95": [r2(x) for x in c["ci95_pct"]["mean"]],
        "p95_overhead_pct": r2(c["overhead_pct"]["p95"]),
        "delta_us": r2(c["delta_mean_us"], 1),
    }


def appendix(run):
    out = {}
    sc = run["scenarios"]
    if "three_arm" in sc:
        arms = sc["three_arm"]["arms"]
        out["three_arm"] = {
            "name_zh": "旧口径对照：明文 FP64 复核 vs 明文同核 vs PPE（同一批查询，三路交叉）",
            "arms_mean_ms": {k: r2(v["latency_us"]["mean"] / 1000, 4) for k, v in arms.items()},
            "hpis_search_mean_us": {k: r2(v["phases_mean_us"]["hpis_search_total"], 1) for k, v in arms.items()},
            "recall_at_10": {k: r2(100 * v["recall_at_k_vs_fp64_truth"]) for k, v in arms.items()},
            "ppe_vs_plaintext_fp64_recheck": comparison(run, "three_arm", "ppe_vs_plaintext_fp64_recheck"),
            "ppe_vs_plaintext_same_kernel": comparison(run, "three_arm", "ppe_vs_plaintext"),
            "plaintext_same_kernel_vs_fp64_recheck": comparison(run, "three_arm", "plaintext_vs_plaintext_fp64_recheck"),
        }
    for key, label in (
        ("refine_strict", "纯密码学严格精排 Top-10（refine=True，HPIS 发送候选密文、HPIC 精排、二次取正文）"),
        ("mapped_exact", "HPIC 预置密态索引严格 Top-10（integration.mapped_index，需 HPIC 预先持有索引副本）"),
    ):
        h = head(run, key)
        if h:
            arms = sc[key]["arms"]
            cipher = next(v for k, v in arms.items() if k != "plaintext")
            h.update(name_zh=label)
            for extra in ("cand_rows_per_query", "cand_bytes_per_query", "cand_wire_bytes_per_query", "refined_rows_per_query"):
                if extra in cipher:
                    h[extra] = r2(cipher[extra], 0)
            out[key] = h
    for key in sc:
        if key.startswith("vec_ppe_b"):
            h = head(run, key)
            if h:
                h["name_zh"] = sc[key]["name_zh"]
                out["ppe_" + key[len("vec_ppe_"):]] = h
    if "mapped" in run.get("setup", {}):
        out.setdefault("mapped_exact", {})["setup"] = run["setup"]["mapped"]
    return out


def io_section(run):
    io = run.get("io")
    if not io:
        return None
    rows = run["dataset"]["rows"]
    full = io["1.0_field"]
    items = [
        ("HPIS 1.0 方式：整个 AID 全量读取并传给 HPIC 解密", full),
        ("2.0 精确字段：只读命中的 1 条", io.get("2.0_field")),
        ("2.0 关键词：只读命中的 10 条", io.get("2.0_word")),
        ("2.0 向量 Top-10：只读命中的 10 条", io.get("2.0_vector")),
    ]
    # /proc/<pid>/io rchar/wchar stay 0 here (SQLite reads via mmap, sockets use send(2)),
    # so only the logical body bytes counted by HPIS are reported.
    detail = {
        k: {kk: vv for kk, vv in v.items() if not kk.startswith("hpis_kernel")}
        for k, v in io.items()
        if isinstance(v, dict)
    }
    out = {
        "title": "每次查询 HPIS 读取并发往 HPIC 的正文字节",
        "desc": (
            f"同一密文租户，{rows:,} 条记录。1.0 方式每次取回并解密整个 AID；2.0 在 HPIS 内选出命中，只读命中的正文。"
            f"向量检索仍在内存中扫描全部 {io['in_memory_vector_index_bytes'] / 1e6:.1f} MB 向量索引，节省的是正文读取、传输与解密。"
        ),
        "rows": [{"label": label, "bytes": int(v["body_bytes"])} for label, v in items if v],
        "full_fetch_mean_ms": r2(full["mean_ms"], 1),
        "partial_fetch_mean_ms": {k.split("_", 1)[1]: r2(v["mean_ms"], 3) for k, v in detail.items() if k.startswith("2.0")},
        "detail": detail,
        "note": io["note"],
    }
    return out


def environment(run):
    env = run["environment"]
    blas = (env.get("blas") or [{}])[0] if env.get("blas") else {}
    ds = run["dataset"]
    return {
        "platform": run.get("platform_label"),
        "cpu": env.get("cpu"),
        "os": f"{env.get('distribution') or ''} · kernel {env.get('kernel')}".strip(" ·"),
        "cpu_flags": env.get("cpu_flags"),
        "cgroup_cpu_max": env.get("cgroup_cpu_max"),
        "memory": env.get("memory"),
        "python": env.get("python"),
        "numpy": env.get("packages", {}).get("numpy"),
        "blas": f"{blas.get('internal_api')} {blas.get('version')} ({blas.get('architecture')}), threads=1" if blas else "single-thread",
        "openssl": env.get("openssl"),
        "dataset": f"{ds['name']} · {ds['rows']:,} 条 × {ds['dims']} 维",
        "pinning": run.get("pinning"),
        "code_sha256": run.get("code"),
    }


def notes(main, appx, scenarios):
    out = []
    t = appx.get("three_arm")
    if t:
        a, b, c = (t["ppe_vs_plaintext_fp64_recheck"], t["ppe_vs_plaintext_same_kernel"], t["plaintext_same_kernel_vs_fp64_recheck"])
        out.append({
            "title": "为什么旧报告里快速向量是 −1.14%",
            "text": (
                f"旧表的明文侧多做了一步 FP64 复核排序，PPE 侧没有。同一批查询三路交叉（三路各用一份数据，互不共享缓存）："
                f"PPE 对“明文+FP64复核”为 {a['overhead_pct']:+.2f}%（[{a['ci95'][0]:+.2f}, {a['ci95'][1]:+.2f}]），"
                f"对同一 FP32 内核的明文为 {b['overhead_pct']:+.2f}%（[{b['ci95'][0]:+.2f}, {b['ci95'][1]:+.2f}]）；"
                f"去掉复核让明文本身快 {-c['overhead_pct']:.2f}%。本报告主表一律用同一内核。"
            ),
        })
    rr = next((x for x in scenarios if x["key"] == "vec_ppe_rerank"), None)
    r = appx.get("refine_strict")
    parts = []
    if rr:
        parts.append(
            f"纯密码学、无需可信硬件的精确 Top-10：PPE 粗排 40 条 + HPIC 精排，{rr['correctness']}，"
            f"开销 {rr['overhead_pct']:+.2f}%（[{rr['ci95'][0]:+.2f}, {rr['ci95'][1]:+.2f}]），代价是每次多读、多解密 30 条正文。"
        )
    if r:
        parts.append(
            f"原有 refine 严格精排（有界证明，与 FP64 真值同序 {r['correctness'].split()[-1]}）为 {r['overhead_pct']:+.1f}%"
            f"（每次约 {r.get('cand_rows_per_query', 0):,.0f} 条候选向量密文、约 {r.get('cand_bytes_per_query', 0) / 1e3:,.0f} KB 发往 HPIC，再二次取正文），不达 10%，列附录。"
        )
    m = appx.get("mapped_exact")
    if m and "overhead_pct" in m:
        parts.append(
            f"HPIC 预置密态索引副本（mapped）严格 Top-10 为 {m['overhead_pct']:+.2f}%（[{m['ci95'][0]:+.2f}, {m['ci95'][1]:+.2f}]），HPIC 授权范围随之扩大。"
        )
    if parts:
        parts.append("TEE 路径（主表第 3 行）在可信计算区内直接得到与明文完全相同的结果。")
        out.append({"title": "严格精排", "text": " ".join(parts)})
    return out


CORE = ("sse_exact", "sse_keyword", "vec_tee", "vec_ppe")


def build(args):
    main = load(args.main)
    if not main:
        raise SystemExit(f"missing main run: {args.main}")
    scale, rep, sec, biz, warm = (load(p) for p in (args.scale, args.replicate, args.secondary, args.business, args.warm))
    earlier = [(Path(p).name, load(p)) for p in args.earlier if load(p)]
    checks = (
        ("scale", scale, "10 万条"),
        ("business_vectors", biz, "业务邮件向量"),
        ("warm_cache", warm, "缓存热口径"),
        ("replicate", rep, "另一批查询"),
        ("secondary_platform", sec, None),
    )
    scenarios = []
    for key in MAIN_KEYS:
        h = short(main, key)
        if not h:
            continue
        item = {"key": key, "name_zh": NAMES[key], "tag": TAGS[key], "desc_zh": DESC[key], "correctness_label": "正确性", **h}
        extra = []
        for label, run, zh in checks:
            s = short(run, key) if run else None
            if s:
                item[label] = s
                if zh:
                    extra.append(f"{zh} {s['overhead_pct']:+.2f}%")
        olds = [dict(run=name, **s) for name, run in earlier if (s := short(run, key))]
        if olds:
            item["earlier_runs"] = olds
        if extra:
            item["desc_zh"] += " 复核：" + "，".join(extra) + "。"
        scenarios.append(item)
    linux_checks = ("scale", "replicate", "business_vectors", "warm_cache")

    def worst_of(s):
        return max([s["overhead_pct"]] + [s[k]["overhead_pct"] for k in linux_checks if k in s])

    core = [s for s in scenarios if s["key"] in CORE]
    core_ok = all(worst_of(s) < 10 for s in core)
    for s in scenarios:
        s["pass"] = s["overhead_pct"] < 10
        s["pass_all_linux_checks"] = worst_of(s) < 10
        # The dashboard hero summarises the SR capabilities; an optional row that misses
        # 10% stays in this list (headline false) and is stated in the notes card.
        s["headline"] = s["key"] in CORE or s["pass_all_linux_checks"]
    failing = [s for s in scenarios if not s["pass_all_linux_checks"]]
    worst = max(s["overhead_pct"] for s in core)
    worst_hi = max(s["ci95"][1] for s in core)
    if not failing:
        title = f"全部 {len(scenarios)} 个密文检索场景的平均端到端时延损耗均低于 10%"
    elif core_ok:
        title = f"{len(core)} 个验收场景（SSE 精确/关键词、TEE、PPE）的平均时延损耗在全部复核中均低于 10%"
    else:
        title = "未全部低于 10%：" + "，".join(f"{s['name_zh']} {worst_of(s):+.2f}%" for s in failing if s in core)
    p = main["parameters"]
    appx = appendix(main)
    out = {
        "schema": "hpis2-acceptance-rollup/1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requirement": "同一 Linux 服务器上：明文存储+明文目标查询 vs 密文存储+密文目标查询，端到端时延增加 < 10%（SSE 精确/关键词检索；基于 PPE 与 TEE 的密态向量检索）",
        "headline": {
            "title": title,
            "subtitle": (
                f"{environment(main)['platform'] or 'Linux'} · {main['environment'].get('cpu')} · "
                f"{main['dataset']['name']} {main['dataset']['rows']:,} 条 · {p['queries']} 个查询 × {p['rounds']} 轮，"
                f"明密文成对交错；最大平均损耗 {worst:+.2f}%，95% 区间上限最高 {worst_hi:+.2f}%"
            ),
            "worst_overhead_pct": r2(worst),
            "worst_ci95_upper_pct": r2(worst_hi),
            "core_scenarios_below_10pct_everywhere": core_ok,
            "all_rows_and_checks_below_10pct": not failing,
            "at_or_above_10pct": [
                {"key": s["key"], "scenario": s["name_zh"], "main_run_overhead_pct": s["overhead_pct"], "worst_check_pct": r2(worst_of(s))}
                for s in failing
            ],
        },
        "environment": environment(main),
        "parameters": {k: p.get(k) for k in ("rows", "queries", "rounds", "k", "beta", "body_bytes", "warmup_pairs_per_block", "run_length", "rerank_k")},
        "method": main["method"],
        "scenarios": scenarios,
        "io": io_section(main),
        "appendix": appx,
        "notes": notes(main, appx, scenarios),
        "runs": {
            name: str(Path(path).resolve().relative_to(HERE.parent)) if path and load(path) else None
            for name, path in (("main", args.main), ("scale", args.scale), ("business_vectors", args.business),
                               ("warm_cache", args.warm), ("replicate", args.replicate),
                               ("secondary_platform", args.secondary))
        },
        "earlier_runs": {
            name: (
                "first full x86 run; its 3-arm appendix let two arms share one plaintext matrix (warm-cache "
                "carry-over), fixed with a separate identical tenant; pairwise main rows kept as a replicate"
                if "superseded" in name else
                "second full x86 run with the fixed 3-arm design, before vec_ppe_rerank was added; kept as a replicate"
            )
            for name, _ in earlier
        },
    }
    if scale:
        out["scale_io"] = io_section(scale)
        out["scale_appendix"] = appendix(scale)
    if biz:
        out["business_io"] = io_section(biz)
        out["business_appendix"] = appendix(biz)
        out["business_dataset"] = biz["dataset"]
    else:
        out["business_vectors"] = {
            "status": "not run",
            "command": (
                "python -m acceptance.run --dataset demo/data/embeddings.f32 --dim 512 --rows <N-544> "
                "--queries 512 --rounds 4 --appendix three_arm --out acceptance/results/linux_x86_business"
            ),
        }
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(out["headline"]["title"])
    for s in scenarios:
        print(f"{s['key']:<15} {s['overhead_pct']:+6.2f}% {s['ci95']}  {s['correctness']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--main", default=RESULTS / "linux_x86_glove20k")
    p.add_argument("--scale", default=RESULTS / "linux_x86_glove100k")
    p.add_argument("--replicate", default=RESULTS / "linux_x86_glove20k_holdout")
    p.add_argument("--secondary", default=RESULTS / "macos_arm64_glove20k")
    p.add_argument("--business", default=RESULTS / "linux_x86_business")
    p.add_argument("--warm", default=RESULTS / "linux_x86_glove20k_warm")
    p.add_argument(
        "--earlier", nargs="*",
        default=[RESULTS / "superseded_linux_x86_glove20k_v0", RESULTS / "linux_x86_glove20k_pre_rerank"],
        help="earlier full runs kept as replicates",
    )
    p.add_argument("--out", default=RESULTS / "summary.json")
    build(p.parse_args())


if __name__ == "__main__":
    main()
